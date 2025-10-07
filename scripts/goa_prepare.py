#!/usr/bin/env python3
from __future__ import annotations
import argparse
import re
from pathlib import Path
from typing import Optional
import pandas as pd

GOA_REGEX = re.compile(r"(?:unable to locate|gone on arrival|\bgoa\b)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare responder GOA features from transformed SF311 data."
    )
    parser.add_argument(
        "--input",
        default="data/transformed.parquet",
        help="Path to transformed dataset (parquet/jsonl/csv). Default: data/transformed.parquet",
    )
    parser.add_argument(
        "--output",
        default="data/derived/goa_features.parquet",
        help="Output path for enriched dataset. Supports parquet/jsonl/csv based on extension.",
    )
    return parser.parse_args()


def read_dataset(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input dataset not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".jsonl", ".json"}:
        return pd.read_json(path, lines=True)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported input format: {path}")


def write_dataset(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        df.to_parquet(path, index=False)
        return
    if suffix in {".jsonl", ".json"}:
        df.to_json(path, orient="records", lines=True)
        return
    if suffix == ".csv":
        df.to_csv(path, index=False)
        return
    raise ValueError(f"Unsupported output format: {path}")


def build_responder_flag(status_notes: pd.Series) -> pd.Series:
    return status_notes.fillna("").astype(str).str.contains(GOA_REGEX)


def prepare_dataset(df: pd.DataFrame) -> pd.DataFrame:
    if "status_notes" not in df.columns:
        raise KeyError("Expected column 'status_notes' not found in dataset.")
    df = df.copy()
    df["status_notes_clean"] = df["status_notes"].fillna("").astype(str).str.strip()
    df["responder_goa"] = build_responder_flag(df["status_notes"])
    return df


def summarize(df: pd.DataFrame) -> str:
    total = len(df)
    goa_count = int(df["responder_goa"].sum())
    missing_notes = int(df["status_notes"].isna().sum())
    dupes = (
        int(df["request_id"].duplicated().sum()) if "request_id" in df.columns else None
    )
    pct = (goa_count / total * 100) if total else 0.0
    lines = [
        f"rows={total}",
        f"responder_goa={goa_count} ({pct:.2f}%)",
        f"missing_status_notes={missing_notes}",
    ]
    if dupes is not None:
        lines.append(f"duplicate_request_ids={dupes}")
    return " | ".join(lines)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    df = read_dataset(input_path)
    enriched = prepare_dataset(df)
    write_dataset(enriched, output_path)

    summary = summarize(enriched)
    print(f"[goa-prepare] wrote {output_path}")
    print(f"[goa-prepare] {summary}")


if __name__ == "__main__":
    main()
