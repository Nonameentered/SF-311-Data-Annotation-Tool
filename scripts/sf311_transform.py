#!/usr/bin/env python3
"""
sf311_transform.py

- Parses SF311 homelessness data from:
  (a) API wrapper dict whose 'body' is a JSON-array STRING
  (b) Raw JSON array
  (c) JSONL
- Normalizes fields and engineers features
- Writes JSONL (always) and, if requested, Parquet/CSV (requires pandas/pyarrow)

Usage:
  uv run scripts/sf311_transform.py \
    --input data/homeless.txt \
    --jsonl data/transformed.jsonl \
    --parquet data/transformed.parquet \
    --csv data/transformed.csv \
    --size-max 400
"""
from __future__ import annotations
import argparse, json, re, sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd  # <-- pandas included
from rich import print

KEYWORDS = {
    "inject": re.compile(r"\binject(ing|ion)?\b", re.I),
    "needle": re.compile(r"\bneedle(s)?\b", re.I),
    "blocking": re.compile(r"\b(block(ing)?|obstruct(ion|ing))\b", re.I),
    "children": re.compile(r"\b(child|children|stroller)\b", re.I),
    "onramp": re.compile(r"\b(on[- ]?ramp|freeway|highway|interchange)\b", re.I),
    "propane": re.compile(r"\bpropane|butane|tank(s)?\b", re.I),
    "fire": re.compile(r"\b(fire|flame|burn(ing)?)\b", re.I),
    "duplicate": re.compile(r"\bduplicate\b", re.I),
    "unable_to_locate": re.compile(r"\bunable to locate\b", re.I),
    "private_property": re.compile(r"\bprivate property\b", re.I),
    "wheelchair": re.compile(r"\bwheelchair\b", re.I),
    "passed_out": re.compile(r"\b(passed[- ]?out|unconscious)\b", re.I),
}

DT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%Y-%m-%d",
)

PHOTO_KEYS = ("photos", "photo_urls", "media_url", "media_urls", "image_urls")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input", required=True, help="Raw SF311 file (API-wrapped, JSON, or JSONL)"
    )
    ap.add_argument("--jsonl", required=True, help="Output JSONL path")
    ap.add_argument("--parquet", help="Optional Parquet output path")
    ap.add_argument("--csv", help="Optional CSV output path")
    ap.add_argument(
        "--size-max",
        type=float,
        default=400.0,
        help="Clip tag_size_feet to [0, size-max] (default: 400)",
    )
    return ap.parse_args()


def load_records(path: Path) -> List[Dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    # Attempt API wrapper → 'body' is a JSON-array string
    try:
        outer = json.loads(raw)
        if isinstance(outer, dict) and isinstance(outer.get("body"), str):
            body = outer["body"].strip()
            if body.startswith("[") and body.endswith("]"):
                return json.loads(body)
    except Exception:
        pass
    # Attempt JSON array
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
    except Exception:
        pass
    # Attempt JSONL
    recs: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                recs.append(obj)
        except Exception:
            continue
    if recs:
        return recs
    raise ValueError("Could not parse input as API-wrapped body, JSON array, or JSONL.")


def to_bool(x: Any) -> Optional[bool]:
    if isinstance(x, bool):
        return x
    if x is None:
        return None
    s = str(x).strip().lower()
    if s in {"true", "t", "yes", "y", "1"}:
        return True
    if s in {"false", "f", "no", "n", "0"}:
        return False
    return None


def to_num(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    m = re.search(r"[-+]?\d+(\.\d+)?", str(x))
    return float(m.group(0)) if m else None


def parse_dt(x: Any) -> Optional[str]:
    if not x:
        return None
    s = str(x)
    for fmt in DT_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.isoformat()
        except Exception:
            continue
    return None


def has_photo(rec: Dict[str, Any]) -> bool:
    for k in PHOTO_KEYS:
        v = rec.get(k)
        if isinstance(v, list) and v:
            return True
        if isinstance(v, str) and v.strip():
            return True
    return False


def extract_text_feats(txt: str) -> Dict[str, Any]:
    if not txt:
        feats = {"desc_len": 0}
        feats.update({f"kw_{k}": False for k in KEYWORDS})
        return feats
    feats = {"desc_len": len(txt)}
    for k, pat in KEYWORDS.items():
        feats[f"kw_{k}"] = bool(pat.search(txt))
    return feats


def normalize_record(rec: Dict[str, Any], size_max: float) -> Dict[str, Any]:
    tags = rec.get("homeless_tags") or {}
    text = (rec.get("description") or "").strip()
    feats = extract_text_feats(text)
    out = {
        "request_id": rec.get("service_request_id") or rec.get("id"),
        "created_at": parse_dt(
            rec.get("requested_datetime")
            or rec.get("created_at")
            or rec.get("createdDate")
        ),
        "status": rec.get("status"),
        "status_notes": rec.get("status_notes") or rec.get("statusNotes"),
        "police_district": rec.get("police_district") or rec.get("policeDistrict"),
        "lat": to_num(rec.get("lat") or rec.get("latitude")),
        "lon": to_num(rec.get("long") or rec.get("lon") or rec.get("longitude")),
        "has_photo": has_photo(rec),
        "text": text if text else None,
        **feats,
        "tag_safety_issue": to_bool(tags.get("safety_issue")),
        "tag_drugs": to_bool(tags.get("drugs")),
        "tag_person_position": (
            str(tags.get("person_position")).strip().lower()
            if tags.get("person_position") is not None
            else None
        ),
        "tag_lying_face_down": to_bool(tags.get("person_lying_face_down_on_sidewalk")),
        "tag_tents_present": to_bool(tags.get("tents_or_makeshift_present")),
        "tag_size_feet": to_num(tags.get("size_feet")),
        "tag_num_people": to_num(tags.get("num_people")),
        "derived_is_private_property": feats.get("kw_private_property", False),
    }
    # Clip noisy fields
    if out["tag_size_feet"] is not None:
        out["tag_size_feet"] = max(
            0.0, min(float(out["tag_size_feet"]), float(size_max))
        )
    if out["tag_num_people"] is not None:
        out["tag_num_people"] = max(0.0, min(float(out["tag_num_people"]), 25.0))
    return out


def main():
    args = parse_args()
    inp = Path(args.input)
    out_jsonl = Path(args.jsonl)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)

    raw_records = load_records(inp)
    rows = [normalize_record(r, args.size_max) for r in raw_records]

    # JSONL
    with out_jsonl.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"[green][ok][/green] wrote JSONL: {out_jsonl} ({len(rows):,} rows)")

    # Optional DataFrame exports
    df = pd.DataFrame(rows)
    if args.parquet:
        out_parquet = Path(args.parquet)
        out_parquet.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_parquet, index=False)  # needs pyarrow
        print(f"[green][ok][/green] wrote Parquet: {out_parquet}")
    if args.csv:
        out_csv = Path(args.csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_csv, index=False)
        print(f"[green][ok][/green] wrote CSV: {out_csv}")


if __name__ == "__main__":
    main()
