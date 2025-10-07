#!/usr/bin/env python3
"""Photo-related GOA analysis (status mix, districts, cues, regression, resolution bins)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import statsmodels.api as sm

from goa_labels import FEATURE_LABELS, STATUS_LABELS

DEFAULT_FEATURES_PATH = Path("data/derived/goa_features.parquet")
DEFAULT_OUTPUT_DIR = Path("data/reports")

STATUS_ORDER = [
    "Case Resolved",
    "Unable to Locate",
    "Other Closed Notes",
    "Open",
    "Unknown",
]

CUE_LABELS = {
    "tag_tents_present": "Tents present",
    "kw_blocking": "Blocking language",
    "tag_num_people": "People reported (>0)",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GOA photo analysis")
    parser.add_argument(
        "--features",
        default=str(DEFAULT_FEATURES_PATH),
        help="Path to derived GOA feature parquet",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for CSV outputs",
    )
    return parser.parse_args()


def load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Feature file not found: {path}. Run make goa-data first.")
    return pd.read_parquet(path)


def bucket_status(status: str, note: str) -> str:
    status_lower = (status or "").lower()
    note_lower = (note or "").lower()
    if status_lower == "open":
        return "Open"
    if not note_lower.strip():
        return "Unknown"
    if "unable to locate" in note_lower or "goa" in note_lower:
        return "Unable to Locate"
    if note_lower.startswith("case resolved"):
        return "Case Resolved"
    return "Other Closed Notes"


def prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["status"] = df["status"].fillna("open").astype(str).str.lower()
    df["status_notes_clean"] = df["status_notes_clean"].fillna("").astype(str)
    df["status_bucket"] = [bucket_status(s, n) for s, n in zip(df["status"], df["status_notes_clean"])]
    df["has_photo_flag"] = df["has_photo"].fillna(False).astype(bool)
    df["hours_to_resolution"] = pd.to_numeric(df["hours_to_resolution"], errors="coerce")
    df["tag_tents_present"] = df["tag_tents_present"].fillna(False).astype(bool)
    df["kw_blocking"] = df["kw_blocking"].fillna(False).astype(bool)
    df["tag_num_people"] = pd.to_numeric(df["tag_num_people"], errors="coerce").fillna(0)
    df["police_district"] = df["police_district"].fillna("Unknown")
    return df


def analysis_status_buckets(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    grouped = (
        df.groupby(["has_photo_flag", "status_bucket"])
        .agg(count=("request_id", "count"))
        .reset_index()
    )
    totals = df.groupby("has_photo_flag")["request_id"].count().rename("total")
    grouped = grouped.merge(totals, on="has_photo_flag")
    grouped["share_pct"] = grouped["count"] / grouped["total"] * 100
    grouped["has_photo_label"] = grouped["has_photo_flag"].map({True: "With photo", False: "No photo"})
    grouped = grouped[["has_photo_label", "status_bucket", "count", "share_pct", "total"]]
    grouped.to_csv(out_dir / "photo_status_distribution.csv", index=False)
    return grouped


def analysis_district(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    district = (
        df.groupby(["police_district", "has_photo_flag"])
        .agg(total=("request_id", "count"), goa=("responder_goa", "sum"))
        .reset_index()
    )
    district["goa_rate_pct"] = district["goa"] / district["total"] * 100
    district["has_photo_label"] = district["has_photo_flag"].map({True: "With photo", False: "No photo"})
    district.to_csv(out_dir / "photo_district_goa.csv", index=False)
    return district


def analysis_cues(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    frames = []
    for cue, label in CUE_LABELS.items():
        cue_df = df.copy()
        if cue == "tag_num_people":
            cue_flag = cue_df[cue].fillna(0).gt(0)
        else:
            cue_flag = cue_df[cue].fillna(False).astype(bool)
        cue_df["cue_flag"] = cue_flag
        grouped = (
            cue_df.groupby(["cue_flag", "has_photo_flag"])["responder_goa"]
            .mean()
            .reset_index(name="goa_rate")
        )
        grouped["cue_label"] = label
        grouped["has_photo_label"] = grouped["has_photo_flag"].map({True: "With photo", False: "No photo"})
        grouped["goa_rate_pct"] = grouped["goa_rate"] * 100
        frames.append(grouped)
    result = pd.concat(frames, ignore_index=True)
    result.to_csv(out_dir / "photo_cue_goa.csv", index=False)
    return result


def analysis_resolution_bins(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    bins = [0, 1, 3, 6, 12, 24, 48, 96, float("inf")]
    labels = ["0-1h", "1-3h", "3-6h", "6-12h", "12-24h", "24-48h", "48-96h", "96h+"]
    subset = df[df["hours_to_resolution"].notna()].copy()
    subset["resolution_bin"] = pd.cut(
        subset["hours_to_resolution"], bins=bins, labels=labels, include_lowest=True
    )
    table = (
        subset.groupby(["has_photo_flag", "responder_goa", "resolution_bin"])
        .size()
        .reset_index(name="count")
    )
    table.to_csv(out_dir / "photo_resolution_bins.csv", index=False)
    return table


def write_status_table_md(status_df: pd.DataFrame, output: Path) -> None:
    rows = ["| Status bucket | With photo share | No photo share |", "| --- | ---: | ---: |"]
    for bucket in STATUS_ORDER:
        with_val = status_df[
            (status_df["has_photo_label"] == "With photo")
            & (status_df["status_bucket"] == bucket)
        ]["share_pct"]
        no_val = status_df[
            (status_df["has_photo_label"] == "No photo")
            & (status_df["status_bucket"] == bucket)
        ]["share_pct"]
        with_pct = with_val.iloc[0] if not with_val.empty else 0.0
        no_pct = no_val.iloc[0] if not no_val.empty else 0.0
        rows.append(f"| {bucket} | {with_pct:.1f}% | {no_pct:.1f}% |")
    output.write_text("\n".join(rows), encoding="utf-8")


def main() -> None:
    args = parse_args()
    features_path = Path(args.features)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_features(features_path)
    df = prepare_dataframe(df)

    status_df = analysis_status_buckets(df, output_dir)
    district_df = analysis_district(df, output_dir)
    cues_df = analysis_cues(df, output_dir)
    resolution_df = analysis_resolution_bins(df, output_dir)
    write_status_table_md(status_df, Path("docs/_photo_status_table.md"))
    print("photo analysis completed")
    print("status distribution saved to", output_dir / "photo_status_distribution.csv")
    print("district GOA saved to", output_dir / "photo_district_goa.csv")
    print("cue GOA saved to", output_dir / "photo_cue_goa.csv")
    print("resolution bins saved to", output_dir / "photo_resolution_bins.csv")


if __name__ == "__main__":
    main()
