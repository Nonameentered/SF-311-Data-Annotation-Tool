#!/usr/bin/env python3
"""Export Supabase labels to JSONL and CSV for backups and analysis."""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback
    import tomli as tomllib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from supabase import Client, create_client

DEFAULT_CHUNK = 1000

GOA_WINDOW_OPTIONS = (
    ("unknown", "Unsure"),
    ("respond_sub2h", "Respond within 2h to avoid GOA"),
    ("respond_2_6h", "Respond within 6h to avoid GOA"),
    ("respond_6_24h", "Respond within 24h to avoid GOA"),
    ("respond_over_24h", "Low GOA risk (>24h)"),
)
GOA_WINDOW_LABELS = {value: label for value, label in GOA_WINDOW_OPTIONS}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dump the Supabase labels table to JSONL/CSV for analysis."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("SUPABASE_URL"),
        help="Supabase project URL (defaults to SUPABASE_URL env var).",
    )
    parser.add_argument(
        "--secret-key",
        default=os.getenv("SUPABASE_SECRET_KEY"),
        help="Supabase secret key (defaults to SUPABASE_SECRET_KEY env var).",
    )
    parser.add_argument(
        "--output-dir",
        default="data/exports",
        help="Directory where the export folder will be created (default: data/exports).",
    )
    parser.add_argument(
        "--secrets-file",
        default=None,
        help="Optional TOML file containing SUPABASE_URL / SUPABASE_SECRET_KEY (falls back to environment).",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Optional subfolder prefix (defaults to current UTC date YYYYMMDD).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK,
        help=f"Batch size per Supabase request (default: {DEFAULT_CHUNK}).",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Optional ISO timestamp; when provided, only labels with timestamp >= value are exported.",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Skip writing JSONL (write CSV only).",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip writing CSV (write JSONL only).",
    )
    return parser.parse_args()


def init_client(url: Optional[str], key: Optional[str]) -> Client:
    if not url:
        raise ValueError("Supabase URL is required (pass --url or set SUPABASE_URL).")
    if not key:
        raise ValueError(
            "Supabase secret key is required (pass --secret-key or set SUPABASE_SECRET_KEY)."
        )
    return create_client(url, key)


def maybe_load_supabase_from_toml(
    secrets_file: Optional[str],
) -> Dict[str, Optional[str]]:
    if not secrets_file:
        return {"url": None, "key": None}
    path = Path(secrets_file).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Secrets file not found: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    url = data.get("SUPABASE_URL")
    key = data.get("SUPABASE_SECRET_KEY")
    return {"url": url, "key": key}


def fetch_labels(
    client: Client, chunk_size: int, since_ts: Optional[str]
) -> Iterable[Dict[str, Any]]:
    start = 0
    end = chunk_size - 1
    while True:
        query = client.table("labels").select("*").range(start, end)
        if since_ts:
            query = query.gte("timestamp", since_ts)
        result = query.execute()
        data = result.data or []
        if not data:
            break
        for row in data:
            yield row
        if len(data) < chunk_size:
            break
        start += chunk_size
        end += chunk_size


def ensure_output_dir(base_dir: str, prefix: Optional[str]) -> Path:
    root = Path(base_dir)
    folder = prefix or datetime.utcnow().strftime("%Y%m%d")
    out_dir = root / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def resolve_goa_window(features: Dict[str, Any]) -> str:
    raw = features.get("goa_window") if isinstance(features, dict) else None
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in GOA_WINDOW_LABELS:
            return normalized
    return "unknown"


def flatten_row(row: Dict[str, Any]) -> Dict[str, Any]:
    features = row.get("features") or {}
    if isinstance(features, str):
        try:
            features = json.loads(features)
        except json.JSONDecodeError:
            features = {}
    follow_up = row.get("follow_up_need")
    if isinstance(follow_up, str):
        follow_up = [follow_up]
    elif not isinstance(follow_up, list):
        follow_up = []
    timestamp = row.get("timestamp")
    if isinstance(timestamp, datetime):
        timestamp = timestamp.astimezone(timezone.utc).isoformat()
    observed_options = [
        ("lying_face_down", "Person lying face down"),
        ("safety_issue", "Immediate safety issue"),
        ("drugs", "Drug use or paraphernalia"),
        ("blocking", "Blocking right-of-way"),
        ("on_ramp", "Near freeway on/off ramp"),
        ("propane_or_flame", "Propane, open flame, or generator"),
        ("children_present", "Children present"),
        ("wheelchair", "Mobility device mentioned"),
    ]
    observed = [label for key, label in observed_options if features.get(key)]
    goa_window = resolve_goa_window(features)
    goa_label = GOA_WINDOW_LABELS.get(
        goa_window, goa_window.replace("_", " ").title()
    )
    return {
        "label_id": row.get("label_id"),
        "request_id": row.get("request_id"),
        "annotator_uid": row.get("annotator_uid"),
        "annotator_email": row.get("annotator_email"),
        "role": row.get("role"),
        "priority": row.get("priority"),
        "tents_count": features.get("tents_count"),
        "goa_window": goa_window,
        "goa_window_label": goa_label,
        "routing_department": features.get("routing_department"),
        "routing_other": features.get("routing_other"),
        "num_people_bin": features.get("num_people_bin"),
        "size_feet_bin": features.get("size_feet_bin"),
        "observed_conditions": ";".join(observed),
        "outcome_alignment": row.get("outcome_alignment"),
        "follow_up_need": ";".join(follow_up or []),
        "notes": row.get("notes"),
        "review_status": row.get("review_status"),
        "review_notes": row.get("review_notes"),
        "timestamp": timestamp,
    }


def write_jsonl(rows: Iterable[Dict[str, Any]], path: Path) -> None:
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    if count == 0:
        path.unlink(missing_ok=True)


def write_csv(rows: Iterable[Dict[str, Any]], path: Path) -> None:
    columns = [
        "label_id",
        "request_id",
        "annotator_uid",
        "annotator_email",
        "role",
        "priority",
        "tents_count",
        "goa_window",
        "goa_window_label",
        "routing_department",
        "routing_other",
        "num_people_bin",
        "size_feet_bin",
        "observed_conditions",
        "outcome_alignment",
        "follow_up_need",
        "notes",
        "review_status",
        "review_notes",
        "timestamp",
    ]
    rows = list(rows)
    if not rows:
        if path.exists():
            path.unlink()
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    secrets_from_file = maybe_load_supabase_from_toml(args.secrets_file)
    url = args.url or secrets_from_file.get("url") or os.getenv("SUPABASE_URL")
    secret_key = (
        args.secret_key
        or secrets_from_file.get("key")
        or os.getenv("SUPABASE_SECRET_KEY")
    )
    client = init_client(url, secret_key)

    export_dir = ensure_output_dir(args.output_dir, args.prefix)
    jsonl_path = export_dir / "labels.jsonl"
    csv_path = export_dir / "labels.csv"

    raw_rows: List[Dict[str, Any]] = []
    flattened: List[Dict[str, Any]] = []

    for row in fetch_labels(client, args.chunk_size, args.since):
        raw_rows.append(row)
        flattened.append(flatten_row(row))

    if not args.no_json:
        write_jsonl(raw_rows, jsonl_path)
    if not args.no_csv:
        write_csv(flattened, csv_path)

    total = len(flattened)
    summary = f"Exported {total} label{'s' if total != 1 else ''} to {export_dir}"
    print(summary)


if __name__ == "__main__":
    main()
