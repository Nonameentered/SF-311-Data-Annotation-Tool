#!/usr/bin/env python3
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd

try:
    import matplotlib.pyplot as plt  # type: ignore
except ImportError:  # pragma: no cover
    plt = None


def rel_to_docs(path: Path) -> str:
    docs_root = Path("docs").resolve()
    try:
        rel = path.resolve().relative_to(docs_root)
        return f"../{rel.as_posix()}"
    except ValueError:
        return path.as_posix()



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble a comprehensive GOA analysis report."
    )
    parser.add_argument(
        "--features",
        default="data/derived/goa_features.parquet",
        help="Prepared GOA dataset (default: data/derived/goa_features.parquet).",
    )
    parser.add_argument(
        "--report-dir",
        default="data/reports",
        help="Directory containing intermediate CSV outputs (default: data/reports).",
    )
    parser.add_argument(
        "--output",
        default="docs/goa_analysis_report.md",
        help="Markdown file to write (default: docs/goa_analysis_report.md).",
    )
    parser.add_argument(
        "--daily-plot",
        default="data/reports/goa_daily_rate.png",
        help="Path for the daily GOA rate plot (default: data/reports/goa_daily_rate.png).",
    )
    parser.add_argument(
        "--preview-days",
        type=int,
        default=14,
        help="Number of most recent daily rows to display (default: 14).",
    )
    parser.add_argument(
        "--top-notes",
        type=int,
        default=10,
        help="Number of status notes to display (default: 10).",
    )
    parser.add_argument(
        "--asset-dir",
        default=None,
        help="Optional directory for chart assets (defaults to report-dir).",
    )
    return parser.parse_args()


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required artifact missing: {path}")
    return pd.read_csv(path)


def df_to_markdown_table(df: pd.DataFrame, float_cols: List[str] | None = None) -> str:
    float_cols = float_cols or []
    headers = list(df.columns)
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for _, row in df.iterrows():
        values = []
        for col in headers:
            val = row[col]
            if col in float_cols and pd.notna(val):
                values.append(f"{val:.2f}")
            else:
                values.append(str(val))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def render_daily_plot(daily: pd.DataFrame, rolling_window: int, output_path: Path) -> bool:
    if plt is None:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(8, 4.5))
    plt.plot(daily["created_date"], daily["goa_rate"] * 100, label="Daily GOA rate")
    if "goa_rate_roll" in daily.columns:
        plt.plot(
            daily["created_date"],
            daily["goa_rate_roll"] * 100,
            label=f"{rolling_window}-day rolling avg",
        )
    plt.xticks(rotation=45, ha="right")
    plt.ylabel("GOA rate (%)")
    plt.tight_layout()
    plt.legend()
    plt.grid(alpha=0.2)
    plt.savefig(output_path, dpi=150)
    plt.close()
    return True


def main() -> None:
    args = parse_args()
    report_dir = Path(args.report_dir)
    asset_dir = Path(args.asset_dir) if args.asset_dir else report_dir
    asset_dir.mkdir(parents=True, exist_ok=True)

    overview = read_csv(report_dir / "goa_overview.csv")
    status_dist = read_csv(report_dir / "goa_status_distribution.csv")
    status_goa = read_csv(report_dir / "goa_status_goa_rates.csv")
    top_status_notes = read_csv(report_dir / "goa_top_status_notes.csv")
    top_status_notes_non_goa = read_csv(report_dir / "goa_top_status_notes_non_goa.csv")
    status_notes_distribution = read_csv(
        report_dir / "goa_status_notes_distribution.csv"
    )
    goa_regex_candidates = read_csv(
        report_dir / "goa_status_notes_regex_candidates.csv"
    )
    daily_rates = read_csv(report_dir / "goa_daily_rates.csv")
    weekly_rates = read_csv(report_dir / "goa_weekly_rates.csv")
    resolution_stats = read_csv(report_dir / "goa_resolution_hours_stats.csv")
    resolution_hist = read_csv(report_dir / "goa_resolution_hours_hist.csv")
    feature_binary = read_csv(report_dir / "goa_feature_binary_summary.csv")
    feature_numeric = read_csv(report_dir / "goa_feature_numeric_bins.csv")

    daily_rates["created_date"] = pd.to_datetime(daily_rates["created_date"])
    daily_rates = daily_rates.sort_values("created_date")
    daily_plot_path = Path(args.daily_plot) if args.daily_plot else asset_dir / "goa_daily_rate.png"
    plot_generated = render_daily_plot(
        daily_rates, rolling_window=7, output_path=daily_plot_path
    )

    preview_daily = daily_rates.tail(args.preview_days).copy()
    preview_daily["created_date"] = preview_daily["created_date"].dt.strftime("%Y-%m-%d")
    preview_daily["goa_rate_pct"] = preview_daily["goa_rate"] * 100
    if "goa_rate_roll" in preview_daily.columns:
        preview_daily["goa_rate_roll_pct"] = preview_daily["goa_rate_roll"] * 100

    weekly_rates["week_start"] = pd.to_datetime(weekly_rates["week_start"]).dt.strftime(
        "%Y-%m-%d"
    )
    weekly_rates["goa_rate_pct"] = weekly_rates["goa_rate"] * 100

    feature_true = feature_binary[
        feature_binary["value"].astype(str).str.lower().eq("true")
    ].copy()
    feature_true = feature_true.sort_values("delta_pp", ascending=False)
    top_positive = feature_true.head(8)[
        ["feature", "count", "goa_rate_pct", "delta_pp", "odds_ratio"]
    ]
    top_negative = feature_true.tail(8).sort_values("delta_pp")[
        ["feature", "count", "goa_rate_pct", "delta_pp", "odds_ratio"]
    ]

    desc_bins = feature_numeric[feature_numeric["feature"] == "desc_len"][
        ["bin_label", "count", "goa_rate_pct", "share_pct"]
    ]

    total_requests = int(overview.loc[0, "total_rows"])
    goa_count = int(overview.loc[0, "responder_goa"])
    goa_pct = overview.loc[0, "responder_goa_pct"]

    unable_share = (
        top_status_notes.loc[top_status_notes["status_note"] == "Unable to Locate.", "share_pct"].iloc[0]
        if "Unable to Locate." in top_status_notes["status_note"].values
        else 0
    )

    latest_date = daily_rates["created_date"].max().date()
    today = datetime.now(timezone.utc).date()
    horizon_days = (today - latest_date).days

    exec_summary = [
        f"- **{goa_pct:.2f}%** of the {total_requests:,} requests end in responder GOA ({goa_count:,} cases).",
        f"- 'Unable to Locate.' accounts for **{unable_share:.2f}%** of all status notes, confirming it as the primary GOA driver.",
        f"- GOA rate dropped from {weekly_rates['goa_rate_pct'].iloc[0]:.2f}% (week of {weekly_rates['week_start'].iloc[0]}) to {weekly_rates['goa_rate_pct'].iloc[-1]:.2f}% (week of {weekly_rates['week_start'].iloc[-1]}).",
        f"- GOA closures resolve more slowly (median ~{resolution_stats.loc[resolution_stats['responder_goa_flag'] == True, '50%'].iloc[0]:.2f} hours) than non-GOA cases (~{resolution_stats.loc[resolution_stats['responder_goa_flag'] == False, '50%'].iloc[0]:.2f} hours).",
    ]
    if horizon_days <= 2:
        exec_summary.append(
            f"- Latest data point is {horizon_days} day{'s' if horizon_days != 1 else ''} old ({latest_date}); expect revisions as new closures arrive."
        )

    positive_plot = asset_dir / "goa_feature_positive.png"
    positive_plot_rel = rel_to_docs(positive_plot) if positive_plot.exists() else None
    negative_plot = asset_dir / "goa_feature_negative.png"
    negative_plot_rel = rel_to_docs(negative_plot) if negative_plot.exists() else None

    report_lines = [
        "# SF311 Gone-On-Arrival Analysis",
        "",
        f"_Compiled on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}_",
        "",
        "## Executive Summary",
        "",
        *exec_summary,
        "",
        "## Responder Outcomes",
        "",
        "The responder dataset is dominated by completed closures; every recorded GOA appears within the 'closed' bucket, reinforcing that GOA is a closure outcome rather than an intermediate state.",
        df_to_markdown_table(
            status_dist[["status", "count", "share_pct"]], float_cols=["share_pct"]
        ),
        "",
        "Within closed cases, nearly a third resolve as GOA. The table below shows responder-level hit rates by status.",
        df_to_markdown_table(
            status_goa[["status", "total", "goa_count", "goa_rate_pct"]],
            float_cols=["goa_rate_pct"],
        ),
        "",
        "### Top Status Notes",
        "",
        df_to_markdown_table(
            top_status_notes.head(args.top_notes)[["status_note", "count", "share_pct"]],
            float_cols=["share_pct"],
        ),
        "",
        "### Top Non-GOA Status Notes",
        "",
        df_to_markdown_table(
            top_status_notes_non_goa.head(args.top_notes)[
                ["status_note", "count", "share_pct"]
            ],
            float_cols=["share_pct"],
        ),
        "",
        "### GOA Regex Candidates",
        "",
        df_to_markdown_table(
            goa_regex_candidates[["status_note", "count", "share_pct"]],
            float_cols=["share_pct"],
        )
        if not goa_regex_candidates.empty
        else "No new GOA phrases observed beyond the current regex.",
        "",
        "## Temporal Trends",
        "",
        f"![Daily GOA Rate]({rel_to_docs(daily_plot_path)})"
        if plot_generated
        else "_(Install matplotlib to render the daily GOA rate plot.)_",
        "",
        df_to_markdown_table(
            preview_daily[
                [
                    "created_date",
                    "total",
                    "goa_count",
                    "goa_rate_pct",
                    "goa_rate_roll_pct",
                ]
            ],
            float_cols=["goa_rate_pct", "goa_rate_roll_pct"],
        ),
        "",
        df_to_markdown_table(
            weekly_rates[["week_start", "total", "goa_count", "goa_rate_pct"]],
            float_cols=["goa_rate_pct"],
        ),
        "",
        "## Resolution Timing",
        "",
        "Responder logs show a stark separation in resolution time: most non-GOA calls close within an hour, while GOA calls frequently linger for half a day or more before being recorded as unresolved.",
        df_to_markdown_table(
            resolution_stats[[
                "responder_goa_flag",
                "count",
                "mean",
                "50%",
                "75%",
                "max",
            ]],
            float_cols=["mean", "50%", "75%", "max"],
        ),
        "",
        df_to_markdown_table(
            resolution_hist[["responder_goa", "bin_label", "count", "share_pct"]],
            float_cols=["share_pct"],
        ),
        "",
        "## Feature Signals",
        "",
        "The following tables highlight the strongest predictors of GOA from the existing tags and keywords.",
        df_to_markdown_table(
            top_positive, float_cols=["goa_rate_pct", "delta_pp", "odds_ratio"]
        ),
        "",
        df_to_markdown_table(
            top_negative, float_cols=["goa_rate_pct", "delta_pp", "odds_ratio"]
        ),
        "",
    ]
    if positive_plot_rel:
        report_lines.extend([
            f"![Top positive GOA signals]({positive_plot_rel})",
            "",
        ])
    if negative_plot_rel:
        report_lines.extend([
            f"![Largest negative GOA deltas]({negative_plot_rel})",
            "",
        ])
    report_lines.extend([
        "### Description Length vs GOA",
        "",
        df_to_markdown_table(
            desc_bins[["bin_label", "count", "goa_rate_pct", "share_pct"]],
            float_cols=["goa_rate_pct", "share_pct"],
        ),
        "",
        "## Data & Methods",
        "",
        "- Data source: `data/derived/goa_features.parquet` generated via `make goa-data`.",
        "- Exploratory artifacts: `make goa-eda`, `make goa-trends`, `make goa-features`.",
        "- GOA flag derives from responder status notes containing 'Unable to Locate', 'GOA', or 'Gone on Arrival'.",
        "- Rolling rates use a 7-day window; recent days may be incomplete if new closures are still logging.",
        "",
        "## Attachment Index",
        "",
    ])

    attachments = [
        "- `data/reports/goa_overview.csv`",
        "- `data/reports/goa_daily_rates.csv`",
        "- `data/reports/goa_weekly_rates.csv`",
        "- `data/reports/goa_feature_binary_summary.csv`",
        "- `data/reports/goa_feature_numeric_bins.csv`",
        "- `data/reports/goa_resolution_hours_stats.csv`",
        "- `data/reports/goa_resolution_hours_hist.csv`",
        "- `data/reports/goa_status_notes_distribution.csv`",
        "- `data/reports/goa_status_notes_regex_candidates.csv`",
    ]
    if positive_plot.exists():
        attachments.append(f"- `{rel_to_docs(positive_plot)}`")
    if negative_plot.exists():
        attachments.append(f"- `{rel_to_docs(negative_plot)}`")
    if daily_plot_path.exists():
        attachments.append(f"- `{rel_to_docs(daily_plot_path)}`")
    report_lines.extend(attachments)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"[goa-report] wrote {output_path}")


if __name__ == "__main__":
    main()
def rel_to_docs(path: Path) -> str:
    docs_root = Path("docs").resolve()
    try:
        rel = path.resolve().relative_to(docs_root)
        return f"../{rel.as_posix()}"
    except ValueError:
        return path.as_posix()
