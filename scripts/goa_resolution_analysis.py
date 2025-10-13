#!/usr/bin/env python3
"""Summarize resolution timing by responder status and render comparison plots."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

DEFAULT_FEATURES = Path("data/derived/goa_features.parquet")
DEFAULT_OUTPUT = Path("data/reports/status_resolution_summary.csv")
DEFAULT_FIG = Path("docs/assets/goa/goa_resolution_boxplot.png")

STATUS_ORDER: Sequence[str] = (
    "Case Resolved",
    "Unable to Locate",
    "Other Closed Notes",
    "Open",
    "Unknown",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute resolution timing stats and a comparative plot.")
    parser.add_argument("--features", default=str(DEFAULT_FEATURES), help="Path to derived feature parquet.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="CSV output path for summary stats.")
    parser.add_argument("--figure", default=str(DEFAULT_FIG), help="PNG output path for boxplot visualization.")
    parser.add_argument(
        "--min-samples",
        type=int,
        default=5,
        help="Minimum rows required to include a status bucket in the plot.",
    )
    return parser.parse_args()


def bucket_status(status: str, note: str) -> str:
    status_lower = (status or "").strip().lower()
    note_lower = (note or "").strip().lower()
    if status_lower == "open":
        return "Open"
    if not note_lower:
        return "Unknown"
    if "unable to locate" in note_lower or "goa" in note_lower:
        return "Unable to Locate"
    if note_lower.startswith("case resolved"):
        return "Case Resolved"
    return "Other Closed Notes"


def load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Feature parquet not found: {path}. Run make goa-data first.")
    return pd.read_parquet(path)


def assign_status_buckets(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["status"] = result["status"].fillna("open").astype(str)
    result["status_notes_clean"] = result["status_notes_clean"].fillna("").astype(str)
    result["status_bucket"] = [
        bucket_status(status, note)
        for status, note in zip(result["status"], result["status_notes_clean"])
    ]
    return result


def compute_resolution_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = assign_status_buckets(df)
    df["hours_to_resolution"] = pd.to_numeric(df["hours_to_resolution"], errors="coerce")
    df["created_at_dt"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    df["updated_at_dt"] = pd.to_datetime(df.get("updated_at"), errors="coerce", utc=True)

    horizon_candidates = []
    for col in ("updated_at_dt", "created_at_dt"):
        series = df[col]
        if series.notna().any():
            horizon_candidates.append(series.max())
    if horizon_candidates:
        analysis_horizon = max(horizon_candidates)
    else:
        analysis_horizon = pd.Timestamp.now(tz="UTC")

    open_mask = df["status_bucket"] == "Open"
    df.loc[open_mask, "resolution_hours"] = (
        analysis_horizon - df.loc[open_mask, "created_at_dt"]
    ).dt.total_seconds() / 3600.0
    df.loc[~open_mask, "resolution_hours"] = df.loc[~open_mask, "hours_to_resolution"]

    frame = df[["status_bucket", "resolution_hours"]].dropna()
    frame = frame[frame["resolution_hours"] >= 0]
    return frame


def compute_summary(resolution_frame: pd.DataFrame) -> pd.DataFrame:
    if resolution_frame.empty:
        return pd.DataFrame(columns=["status_bucket", "count", "share_pct", "median_hours", "iqr_hours", "p25_hours", "p75_hours"])
    summary = (
        resolution_frame.groupby("status_bucket")["resolution_hours"]
        .agg(
            count="count",
            median_hours="median",
            p25_hours=lambda s: s.quantile(0.25),
            p75_hours=lambda s: s.quantile(0.75),
        )
        .reset_index()
    )
    total = summary["count"].sum()
    summary["iqr_hours"] = summary["p75_hours"] - summary["p25_hours"]
    summary["share_pct"] = summary["count"] / total * 100 if total else 0.0

    category_order = [status for status in STATUS_ORDER if status in summary["status_bucket"].unique()]
    missing = sorted(
        set(summary["status_bucket"]) - set(category_order),
        key=str.lower,
    )
    ordered_cats = category_order + missing
    summary["status_bucket"] = pd.Categorical(summary["status_bucket"], categories=ordered_cats, ordered=True)
    summary = summary.sort_values("status_bucket").reset_index(drop=True)
    numeric_cols = ["median_hours", "iqr_hours", "p25_hours", "p75_hours", "share_pct"]
    summary[numeric_cols] = summary[numeric_cols].round(2)
    summary["count"] = summary["count"].astype(int)
    summary["status_bucket"] = summary["status_bucket"].astype(str)
    return summary[["status_bucket", "count", "share_pct", "median_hours", "iqr_hours", "p25_hours", "p75_hours"]]


def save_summary(summary: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_path, index=False)


def save_boxplot(
    resolution_frame: pd.DataFrame,
    summary: pd.DataFrame,
    output_path: Path,
    min_samples: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if resolution_frame.empty or summary.empty:
        return

    ordered_buckets = summary["status_bucket"].tolist()
    series_list = []
    labels = []
    for bucket in ordered_buckets:
        data = resolution_frame.loc[resolution_frame["status_bucket"] == bucket, "resolution_hours"]
        if len(data) < min_samples:
            continue
        series_list.append(data.values)
        labels.append(bucket)

    if not series_list:
        return

    fig, ax = plt.subplots(figsize=(9, 4.5))
    box = ax.boxplot(
        series_list,
        vert=False,
        tick_labels=labels,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": "#2c3e50", "linewidth": 1.5},
    )

    palette = plt.get_cmap("Blues")(np.linspace(0.35, 0.75, len(series_list)))
    for patch, face_color in zip(box["boxes"], palette):
        patch.set_facecolor(face_color)
        patch.set_alpha(0.8)

    # Cap the horizontal axis at the 95th percentile to avoid long-tail distortion.
    high_water = max(np.percentile(data, 95) for data in series_list)
    xlim_right = high_water * 1.15 if high_water > 0 else 1.0
    ax.set_xlim(left=0, right=xlim_right)

    # Annotate medians beside each box for quick scanning.
    medians = summary.set_index("status_bucket").loc[labels, "median_hours"]
    for median_line, median_val in zip(box["medians"], medians):
        y = median_line.get_ydata().mean()
        ax.text(
            median_val,
            y,
            f"{median_val:.1f}h",
            va="center",
            ha="left" if median_val < xlim_right * 0.9 else "right",
            fontsize=9,
            color="#2c3e50",
        )
    ax.set_xlabel("Hours until closure (or hours open)")
    ax.set_title("Resolution timing by responder status")
    ax.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.6)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def main() -> None:
    args = parse_args()

    features_path = Path(args.features)
    summary_path = Path(args.output)
    figure_path = Path(args.figure)

    features_df = load_features(features_path)
    resolution_frame = compute_resolution_frame(features_df)
    summary = compute_summary(resolution_frame)
    save_summary(summary, summary_path)
    save_boxplot(resolution_frame, summary, figure_path, min_samples=args.min_samples)

    print(f"Resolution stats saved to {summary_path}")
    if summary.empty:
        print("No resolution data available for plotting.")
    else:
        print(f"Boxplot saved to {figure_path}")


if __name__ == "__main__":
    main()
