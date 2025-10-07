#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path
import pandas as pd
from rich import print


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="transformed.{jsonl|parquet|csv}")
    ap.add_argument(
        "--show", type=int, default=10, help="How many sample rows to display"
    )
    ap.add_argument(
        "--snapshot-out",
        help="Optional JSON snapshot path. Default: data/audit/<timestamp>.json",
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

    for col in ["tag_lying_face_down", "tag_person_position", "tag_tents_present"]:
        if col in df.columns:
            print(f"\n[cyan]{col}[/cyan] value counts:")
            print(df[col].value_counts(dropna=False).head(10))

    if {"tag_lying_face_down", "tag_person_position"} <= set(df.columns):
        bad = df[
            (df["tag_lying_face_down"] == True)
            & (~df["tag_person_position"].fillna("").str.contains("lying"))
        ]
        print(f"\n[red]lfd=True but person_position not 'lying'[/red]: {len(bad)}")
        if len(bad) > 0:
            print(bad[["request_id", "tag_person_position", "text"]].head(args.show))

    for col in ["tag_size_feet", "tag_num_people"]:
        if col in df.columns:
            print(f"\n[cyan]{col}[/cyan] describe():")
            print(df[col].describe())

    print("\n[bold]Sample rows[/bold]:")
    pd.set_option("display.max_colwidth", 60)
    print(df.head(args.show).to_string(index=False))

    snapshot_path = args.snapshot_out
    if snapshot_path is None:
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        snapshot_path = Path("data") / "audit" / f"snapshot_{ts}.json"
    else:
        snapshot_path = Path(snapshot_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "generated_at": datetime.utcnow().isoformat(),
        "input": str(path),
        "rows": int(n),
        "has_photo_count": (
            int(df["has_photo"].sum()) if "has_photo" in df.columns else None
        ),
        "nonempty_descriptions": int(nonempty_text),
        "value_counts": {},
        "numeric_stats": {},
    }

    for col in ["tag_lying_face_down", "tag_person_position", "tag_tents_present"]:
        if col in df.columns:
            counts = df[col].value_counts(dropna=False).to_dict()
            snapshot["value_counts"][col] = {str(k): int(v) for k, v in counts.items()}

    for col in ["tag_size_feet", "tag_num_people"]:
        if col in df.columns:
            stats = df[col].describe(include="all").to_dict()
            cleaned = {
                str(k): (float(v) if hasattr(v, "__float__") else str(v))
                for k, v in stats.items()
            }
            snapshot["numeric_stats"][col] = cleaned

    snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"\n[green][ok][/green] Snapshot written to {snapshot_path}")


if __name__ == "__main__":
    main()
