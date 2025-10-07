# Re-parse correctly by extracting and JSON-loading the "body" string, then re-run the audit + transform.
import json, re
from pathlib import Path
import pandas as pd
from datetime import datetime

SRC = Path("/mnt/data/homeless.txt")
OUT = Path("/mnt/data/homeless_transformed.jsonl")

outer = json.loads(SRC.read_text(encoding="utf-8"))
# The actual records live inside the stringified JSON array in 'body'
inner_str = outer.get("body", "").strip()
assert inner_str.startswith("["), "Expected 'body' to be a JSON array string"
records = json.loads(inner_str)

len_records = len(records)
print("Parsed records:", len_records)


# Reuse helper functions from the previous cell by redefining minimal versions here
def to_bool(x):
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


def to_num(x):
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        m = re.search(r"[-+]?\d+(\.\d+)?", str(x))
        return float(m.group(0)) if m else None


def parse_dt(x):
    if not x:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(str(x), fmt)
            return dt.isoformat()
        except Exception:
            continue
    return None


def has_photo(rec):
    for k in ("photos", "photo_urls", "media_url", "media_urls", "image_urls"):
        v = rec.get(k)
        if isinstance(v, list) and len(v) > 0:
            return True
        if isinstance(v, str) and v.strip():
            return True
    return False


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


def extract_text_feats(txt: str):
    if not txt:
        return {"desc_len": 0, **{f"kw_{k}": False for k in KEYWORDS}}
    feats = {"desc_len": len(txt)}
    for k, pat in KEYWORDS.items():
        feats[f"kw_{k}"] = bool(pat.search(txt))
    return feats


def normalize_record(rec):
    tags = rec.get("homeless_tags") or {}
    text = (rec.get("description") or "").strip()
    images = has_photo(rec)

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
        "has_photo": images,
        "text": text if text else None,
        **extract_text_feats(text),
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
        "derived_is_private_property": extract_text_feats(text).get(
            "kw_private_property", False
        ),
    }
    return out


with OUT.open("w", encoding="utf-8") as f:
    for rec in records:
        f.write(json.dumps(normalize_record(rec), ensure_ascii=False) + "\n")

df_preview = pd.read_json(OUT, lines=True).head(50)
display_dataframe_to_user("Preview (first 50) after correct parsing", df_preview)

# Quick audit on key fields:
import math
from collections import Counter

lying = Counter()
positions = Counter()
tents = Counter()
has_photos = 0
nonempty_text = 0
for rec in records:
    tags = rec.get("homeless_tags") or {}
    v = to_bool(tags.get("person_lying_face_down_on_sidewalk"))
    lying[str(v)] += 1
    p = (
        str(tags.get("person_position")).strip().lower()
        if tags.get("person_position") is not None
        else None
    )
    positions[str(p)] += 1
    t = to_bool(tags.get("tents_or_makeshift_present"))
    tents[str(t)] += 1
    if has_photo(rec):
        has_photos += 1
    if rec.get("description"):
        nonempty_text += 1

audit_df = (
    pd.DataFrame(
        {"lying_face_down": lying, "person_position": positions, "tents_present": tents}
    )
    .fillna(0)
    .astype(int)
    .T.reset_index()
    .rename(columns={"index": "field"})
)

display_dataframe_to_user("Key tag distributions (quick tally)", audit_df)
print("Has photos (count):", has_photos, " / ", len_records)
print("Non-empty descriptions (count):", nonempty_text, " / ", len_records)

OUT.as_posix()
