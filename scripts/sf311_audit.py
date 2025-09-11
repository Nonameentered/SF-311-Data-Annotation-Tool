#!/usr/bin/env python3
"""
sf311_audit.py

Pandas-based cleanliness/readiness audit for the transformed JSONL/Parquet/CSV.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd
from rich import print


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="transformed.{jsonl|parquet|csv}")
    ap.add_argument(
        "--show", type=int, default=10, help="How many sample rows to display"
    )
    return ap.parse_args()


def read_any(path: Path) -> pd.DataFrame:
    p = str(path)
    if p.endswith(".jsonl") or p.endswith(".json"):
        return pd.read_json(p, lines=True)
    if p.endswith(".parquet"):
        return pd.read_parquet(p)
    if p.endswith(".csv"):
        return pd.read_csv(p)
    raise ValueError("Unsupported file type: use .jsonl / .parquet / .csv")


def main():
    args = parse_args()
    path = Path(args.input)
    df = read_any(path)
    n = len(df)

    print(f"[bold]Rows[/bold]: {n}")
    print(f"Has photo: {df['has_photo'].sum()} / {n}")
    nonempty_text = df["text"].fillna("").str.len().gt(0).sum()
    print(f"Non-empty descriptions: {nonempty_text} / {n}")

    # Tag distributions
    for col in ["tag_lying_face_down", "tag_person_position", "tag_tents_present"]:
        if col in df.columns:
            print(f"\n[cyan]{col}[/cyan] value counts:")
            print(df[col].value_counts(dropna=False).head(10))

    # Sanity: lfd==True but position doesn't contain 'lying'
    if "tag_lying_face_down" in df.columns and "tag_person_position" in df.columns:
        bad = df[
            (df["tag_lying_face_down"] == True)
            & (~df["tag_person_position"].fillna("").str.contains("lying"))
        ]
        print(f"\n[red]lfd=True but person_position not 'lying'[/red]: {len(bad)}")
        if len(bad) > 0:
            print(bad[["request_id", "tag_person_position", "text"]].head(args.show))

    # Quick numeric summaries
    for col in ["tag_size_feet", "tag_num_people"]:
        if col in df.columns:
            print(f"\n[cyan]{col}[/cyan] describe():")
            print(df[col].describe())

    # Show sample
    print("\n[bold]Sample rows[/bold]:")
    print(df.head(args.show).to_string(index=False, max_colwidth=60))


if __name__ == "__main__":
    main()
