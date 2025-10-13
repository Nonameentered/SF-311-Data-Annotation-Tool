#!/usr/bin/env python3
from __future__ import annotations
import hashlib
import json
import os
import sys
import uuid
from copy import deepcopy
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

APP_TITLE = "SF311 Priority Labeler — Human-in-the-Loop"

# Page config: wide mode by default
try:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
except Exception:
    pass

StreamlitSecretNotFoundError = StreamlitAPIException

try:
    from supabase import Client, create_client
except Exception:  # pragma: no cover - supabase optional
    Client = None
    create_client = None

try:  # pragma: no cover - optional dependency
    from streamlit_shortcuts import button as shortcut_button
except Exception:  # pragma: no cover - optional dependency
    shortcut_button = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from scripts.labeler_utils import (
    can_annotator_label,
    latest_label_for_annotator,
    latest_label_excluding,
    parse_iso,
    request_status,
    sort_labels,
    unique_annotators,
)


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    env_value = os.getenv(key)
    if env_value is not None:
        return env_value
    try:
        secrets_obj = dict(st.secrets)  # type: ignore[arg-type]
    except StreamlitSecretNotFoundError:
        secrets_obj = {}
    except Exception:
        secrets_obj = {}
    value = secrets_obj.get(key, default)
    return str(value) if value is not None else None


DATA = Path(get_secret("LABELER_DATA_DIR", "data"))
RAW = DATA / "transformed.jsonl"
LABELS_DIR = Path(get_secret("LABELS_OUTPUT_DIR", str(DATA / "labels")))
MAX_ANNOTATORS = int(get_secret("MAX_ANNOTATORS_PER_REQUEST", "3"))
SUPABASE_URL = get_secret("SUPABASE_URL")

SUPABASE_PUBLISHABLE_KEY = get_secret("SUPABASE_PUBLISHABLE_KEY")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")
SUPABASE_SECRET_KEY = get_secret("SUPABASE_SECRET_KEY")
if SUPABASE_SECRET_KEY:
    SUPABASE_KEY = SUPABASE_SECRET_KEY
    SUPABASE_KEY_KIND = "secret"
elif SUPABASE_ANON_KEY:
    SUPABASE_KEY = SUPABASE_ANON_KEY
    SUPABASE_KEY_KIND = "anon"
elif SUPABASE_PUBLISHABLE_KEY:
    SUPABASE_KEY = SUPABASE_PUBLISHABLE_KEY
    SUPABASE_KEY_KIND = "publishable"
else:
    SUPABASE_KEY = None
    SUPABASE_KEY_KIND = None

BACKUP_SETTING = get_secret("LABELS_JSONL_BACKUP")
REQUIRED_UNIQUE_FOR_COMPLETION = max(1, min(MAX_ANNOTATORS, 2))
st.markdown(
    """
    <style>
    /* Add enough top padding so content clears the sticky app header */
    .block-container {
        padding-top: 3.5rem !important;
        padding-bottom: 1.6rem !important;
    }
    [data-testid="block-container"] {
        padding-top: 3.5rem !important;
    }
    iframe[src*="streamlit_browser_storage"] {
        display: none !important;
        height: 0 !important;
        margin: 0 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

ShortcutType = Union[str, Sequence[str]]

FEATURE_TIPS: Dict[str, str] = {
    "lying_face_down": "Report indicates someone is prone or face down on the ground.",
    "safety_issue": "Flags imminent safety hazards (traffic, violence, weapons).",
    "drugs": "Evidence of drug use or paraphernalia on scene.",
    "tents_count": "Estimate how many tents or makeshift structures are visible.",
    "blocking": "Belongings or people are obstructing the right of way (sidewalk/road).",
    "on_ramp": "Located on or immediately adjacent to a freeway on/off ramp.",
    "propane_or_flame": "Propane tanks, open flames, or generators noted.",
    "children_present": "Children observed at the scene.",
    "wheelchair": "Wheelchair or mobility device mentioned in the request.",
    "num_people_bin": "Responder estimate of individuals present (HSOC tag or annotator update).",
    "size_feet_bin": "Linear footprint in feet from HSOC responders. Use bins to adjust if photos show otherwise.",
}

FEATURE_DISPLAY_NAMES: Dict[str, str] = {
    "lying_face_down": "Person lying face down",
    "safety_issue": "Immediate safety issue",
    "drugs": "Drug use or paraphernalia",
    "tents_present": "Auto flag: tents present",
    "tents_count": "# of tents",
    "blocking": "Blocking right-of-way",
    "on_ramp": "Near freeway on/off ramp",
    "propane_or_flame": "Propane, open flame, or generator",
    "children_present": "Children present",
    "wheelchair": "Mobility device mentioned",
    "num_people_bin": "Est. # of people",
    "size_feet_bin": "Est. footprint (feet)",
}

PRIORITY_OPTIONS: Sequence[str] = ("High", "Medium", "Low", "Invalid")
PRIORITY_STORAGE = {label: label.lower() for label in PRIORITY_OPTIONS}
PRIORITY_LEGACY_MAP = {
    "p1": "High",
    "p2": "High",
    "p3": "Medium",
    "p4": "Low",
}

GOA_WINDOW_OPTIONS: Sequence[Tuple[str, str]] = (
    ("unknown", "Unsure"),
    ("respond_sub2h", "Respond within 2h to avoid GOA"),
    ("respond_2_6h", "Respond within 6h to avoid GOA"),
    ("respond_6_24h", "Respond within 24h to avoid GOA"),
    ("respond_over_24h", "Low GOA risk (>24h)"),
)
GOA_WINDOW_VALUES = [value for value, _ in GOA_WINDOW_OPTIONS]
GOA_WINDOW_LABELS = {value: label for value, label in GOA_WINDOW_OPTIONS}

ROUTING_DEPARTMENTS: Sequence[str] = (
    "SFHOT",
    "HEART",
    "Street Health",
    "HSOC",
    "DSS",
    "DPW",
    "Unknown",
    "Other",
)

LABEL_TIPS: Dict[str, str] = {
    "priority": "High = immediate response, Medium = timely but not emergent, Low = informational or deferrable.",
    "evidence_sources": "Select the sources you relied on (photos, notes, prior history, etc.).",
    "notes": "Capture rationale, escalation paths, or anomalies for reviewers.",
    "outcome_alignment": "How the observed outcome aligns with expectations or service goals.",
    "outcome_alignment_open": "For open cases, choose the most likely outcome based on current evidence.",
    "follow_up_need": "Additional services that would help this case (multi-select).",
    "routing_department": "Primary team you expect to handle the request.",
    "goa_window": "Estimate when the subject might be gone if a team deploys now. Use the bucket that best fits your expectation.",
    "review_status": "Choose whether you agree with the previous annotator or believe adjustments are needed.",
    "review_notes": "Explain disagreements or add missing context so the prior label can be audited.",
}

OUTCOME_OPTIONS: List[Tuple[str, str]] = [
    ("", "Select outcome alignment"),
    ("service_delivered", "Service delivered / resolved"),
    ("client_declined", "Client declined or not interested"),
    ("unable_to_locate", "Unable to locate client (Gone on arrival)"),
    ("duplicate_report", "Duplicate report"),
    ("no_action_needed", "No action needed"),
    ("invalid_report", "Invalid report / not a 311 issue"),
    ("other", "Other outcome"),
]

FOLLOW_UP_OPTIONS: List[Tuple[str, str]] = [
    ("mental_health", "Mental health support"),
    ("shelter", "Shelter / placement"),
    ("case_management", "Case management"),
    ("medical", "Medical support"),
    ("sanitation", "Sanitation / cleanup"),
    ("legal", "Legal or documentation"),
    ("other", "Other resource"),
]

KEYWORD_FILTER_OPTIONS: List[Tuple[str, str]] = [
    ("passed_out", "Passed out / unresponsive"),
    ("blocking", "Blocking pathway"),
    ("onramp", "Freeway on/off ramp"),
    ("propane", "Propane present"),
    ("fire", "Active flames"),
    ("children", "Children mentioned"),
    ("wheelchair", "Mobility device noted"),
]

TAG_FILTER_OPTIONS: List[Tuple[str, str]] = [
    ("lying_face_down", FEATURE_DISPLAY_NAMES["lying_face_down"]),
    ("tents_present", FEATURE_DISPLAY_NAMES["tents_present"]),
]

STATUS_FILTER_OPTIONS: List[str] = [
    "unlabeled",
    "labeled",
    "all",
]

REVIEW_STATUS_LABELS: Dict[str, str] = {
    "pending": "Not reviewed",
    "agree": "Agree with previous assessment",
    "disagree": "Disagree / needs change",
}


def outcome_display(value: Optional[str]) -> str:
    if not value:
        return "—"
    for key, label in OUTCOME_OPTIONS:
        if key == value:
            return label
    return value.replace("_", " ").title()


def suggest_outcome(record: Dict[str, Any]) -> Tuple[Optional[str], str]:
    text_parts: List[str] = []
    for key in ("status_notes", "resolution_notes", "status"):
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            text_parts.append(val)
    haystack = " ".join(text_parts).lower()
    rules: List[Tuple[str, List[str]]] = [
        (
            "duplicate_report",
            ["duplicate", "dupe", "dup.", "duplicate case"],
        ),
        (
            "invalid_report",
            [
                "invalid",
                "not a case",
                "spam",
                "test case",
                "wrong department",
            ],
        ),
        (
            "unable_to_locate",
            ["gone on arrival", "unable to locate", "no longer there", "goa", "utl"],
        ),
        (
            "service_delivered",
            [
                "service delivered",
                "assisted",
                "provided",
                "resolved",
                "cleaned",
                "completed",
            ],
        ),
        ("client_declined", ["declined", "refused", "not interested"]),
        ("no_action_needed", ["no action needed", "nrn", "no further action"]),
    ]
    for outcome_value, keywords in rules:
        if any(k in haystack for k in keywords):
            return (
                outcome_value,
                f"Suggested from notes: {outcome_value.replace('_', ' ')}",
            )
    return None, ""


def follow_up_display(values: Optional[Sequence[str]]) -> str:
    if not values:
        return "—"
    labels: List[str] = []
    for item in values:
        for key, label in FOLLOW_UP_OPTIONS:
            if key == item:
                labels.append(label)
                break
        else:
            labels.append(str(item))
    return ", ".join(labels)


def resolve_goa_window(features: Dict[str, Any]) -> str:
    raw = features.get("goa_window") if isinstance(features, dict) else None
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in GOA_WINDOW_VALUES:
            return normalized
    return "unknown"


def goa_window_label(value: str) -> str:
    normalized = (value or "").strip().lower()
    return GOA_WINDOW_LABELS.get(normalized, normalized.replace("_", " ").title())


def user_random_value(request_id: Any, annotator_uid: str) -> float:
    base = f"{request_id}:{annotator_uid}".encode("utf-8", "ignore")
    digest = hashlib.sha256(base).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


FIELD_GLOSSARY: Dict[str, str] = {
    "Priority": LABEL_TIPS["priority"],
    "Outcome alignment": LABEL_TIPS["outcome_alignment"],
    "Follow-up needs": LABEL_TIPS["follow_up_need"],
    "Footprint (ft)": "`size_feet` captures the linear spread estimated by responders.",
    "Hours to resolution": "Time between the initial request and the last known update/closure.",
    "Closure notes": "311 or responder notes at closure. Often describe remediation or why the case was closed.",
    "Post-closure notes": "Follow-up notes shared after closure (if available).",
}


def keyboard_button(
    label: str,
    shortcuts: ShortcutType | None = None,
    *,
    button_type: str = "secondary",
    width: str = "content",
    key: Optional[str] = None,
    help: Optional[str] = None,
) -> bool:
    """Render a button with optional keyboard shortcuts."""

    if shortcuts and shortcut_button is not None:
        try:
            return shortcut_button(
                label,
                shortcuts,
                None,
                key=key,
                help=help,
                type=button_type,
                width=width,
                hint=True,
            )
        except TypeError:
            return shortcut_button(
                label,
                shortcuts,
                None,
                key=key,
                help=help,
                type=button_type,
                use_container_width=width == "stretch",
                hint=True,
            )
    try:
        return st.button(
            label,
            key=key,
            help=help,
            type=button_type,
            width=width,
        )
    except TypeError:
        return st.button(
            label,
            key=key,
            help=help,
            type=button_type,
            use_container_width=width == "stretch",
        )


def parse_created_at(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(value, str):
        parsed = parse_iso(value)
        if parsed is not None:
            return parsed
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


@st.cache_data
def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            rows.append(json.loads(s))
    return rows


def coerce_features(entry: Dict[str, Any]) -> Dict[str, Any]:
    features = entry.get("features") or {}
    if isinstance(features, str):
        try:
            features = json.loads(features)
        except json.JSONDecodeError:
            features = {}
    return features if isinstance(features, dict) else {}


def format_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return "—"
    return str(value)


def format_duration_hours(value: Any) -> str:
    if value is None:
        return "—"
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return "—"
    if hours < 1:
        minutes = round(hours * 60)
        return f"{minutes} min"
    if hours < 48:
        return f"{round(hours, 1)} h"
    days = hours / 24.0
    return f"{round(days, 1)} days"


def compute_dataset_cutoff(rows: List[Dict[str, Any]]) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for r in rows:
        ts = parse_created_at(r.get("updated_at")) or parse_created_at(
            r.get("created_at")
        )
        if ts is not None and (latest is None or ts > latest):
            latest = ts
    return latest


@st.cache_resource(show_spinner=False)
def init_supabase_client(url: str, key: str) -> Client:
    if create_client is None:  # pragma: no cover - client optional in dev
        raise RuntimeError("supabase client library not installed")
    return create_client(url, key)


def get_supabase_client() -> Optional[Client]:
    if create_client is None:
        st.error(
            "supabase-py is not installed. Run 'uv add supabase' or sync dependencies via 'make init'."
        )
        return None
    if not SUPABASE_URL:
        st.error(
            "SUPABASE_URL is not configured. Add it to Streamlit secrets or environment variables."
        )
        return None
    if SUPABASE_KEY_KIND != "secret":
        st.error(
            "Provide SUPABASE_SECRET_KEY in secrets/environment so the app can write labels while using external auth."
        )
        return None
    try:
        return init_supabase_client(SUPABASE_URL, SUPABASE_KEY)  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - Supabase failure surfaced in UI
        st.error(f"Failed to initialize Supabase client: {exc}")
        return None


def load_labels_supabase(
    client: Client,
) -> Dict[str, List[Dict[str, Any]]]:  # pragma: no cover - requires Supabase
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        resp = client.table("labels").select("*").execute()
    except Exception as exc:
        st.error(f"Failed to load labels from Supabase: {exc}")
        return out
    for row in resp.data or []:
        rid = row.get("request_id")
        if rid is None:
            continue
        row["features"] = coerce_features(row)
        ts = row.get("timestamp")
        if isinstance(ts, datetime):
            row["timestamp"] = ts.isoformat()
        follow_raw = row.get("follow_up_need")
        if isinstance(follow_raw, str):
            try:
                parsed_follow = json.loads(follow_raw)
                row["follow_up_need"] = (
                    parsed_follow if isinstance(parsed_follow, list) else []
                )
            except json.JSONDecodeError:
                row["follow_up_need"] = [follow_raw]
        elif isinstance(follow_raw, list):
            row["follow_up_need"] = follow_raw
        else:
            row["follow_up_need"] = []
        out.setdefault(str(rid), []).append(row)
    return out


def todays_label_file() -> Path:
    day = datetime.utcnow().strftime("%Y%m%d")
    run_dir = LABELS_DIR / day
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / "labels.jsonl"


def save_label(
    payload: Dict[str, Any], supabase_client: Client, enable_file_backup: bool
) -> bool:
    try:  # pragma: no cover - requires Supabase
        supabase_client.table("labels").insert(payload).execute()
    except Exception as exc:
        st.error(f"Failed to write label to Supabase: {exc}")
        return False
    if enable_file_backup:
        target = todays_label_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return True


def delete_label(label_id: str, supabase_client: Client) -> bool:
    try:  # pragma: no cover - requires Supabase
        supabase_client.table("labels").delete().eq("label_id", label_id).execute()
        return True
    except Exception as exc:
        st.error(f"Failed to undo label: {exc}")
        return False


def resolve_images(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    urls = row.get("image_urls") or []
    paths = row.get("image_paths") or []
    checksums = row.get("image_checksums") or []
    statuses = row.get("image_fetch_status") or []
    resolved: List[Dict[str, Any]] = []
    for idx, url in enumerate(urls):
        local_path = None
        if idx < len(paths) and paths[idx]:
            p = Path(paths[idx])
            if p.exists():
                local_path = str(p)
        resolved.append(
            {
                "url": url,
                "local_path": local_path,
                "checksum": checksums[idx] if idx < len(checksums) else None,
                "status": statuses[idx] if idx < len(statuses) else None,
            }
        )
    return resolved


def rich_context_score(record: Dict[str, Any]) -> float:
    score = 0.0
    if record.get("has_photo"):
        images = record.get("image_urls") or []
        score += min(len(images), 6) * 1.5
    if record.get("status_notes"):
        score += 2.0
    if record.get("resolution_notes"):
        score += 1.5
    if record.get("after_action_url"):
        score += 1.0
    if record.get("hours_to_resolution"):
        score += 1.0
    if record.get("tag_size_feet"):
        score += 0.5
    if record.get("tag_num_people"):
        score += 0.5
    return score


@st.cache_data(show_spinner=False)
def compute_dataset_hash(path: Path) -> str:
    if not path.exists():
        return "missing"
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        # Fallback to size+mtime if content hashing fails
        try:
            stt = path.stat()
            sig = f"{stt.st_size}:{int(stt.st_mtime)}".encode("utf-8")
            return hashlib.sha256(sig).hexdigest()
        except Exception:  # noqa: BLE001
            return str(uuid.uuid4())


# Supabase helpers for per-user queues
def load_saved_queue(
    client: Client, annotator_uid: str, dataset_hash: str
) -> Optional[Dict[str, Any]]:
    try:  # pragma: no cover - requires Supabase
        resp = (
            client.table("annotator_queues")
            .select("*")
            .eq("annotator_uid", annotator_uid)
            .eq("dataset_hash", dataset_hash)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return rows[0]
        return None
    except Exception as exc:
        st.error(f"Failed to load saved queue: {exc}")
        return None


def save_queue(
    client: Client,
    annotator_uid: str,
    dataset_hash: str,
    queue: List[str],
    position: int = 0,
) -> bool:
    payload = {
        "annotator_uid": annotator_uid,
        "dataset_hash": dataset_hash,
        "queue": queue,
        "position": int(position),
    }
    try:  # pragma: no cover - requires Supabase
        client.table("annotator_queues").upsert(payload).execute()
        return True
    except Exception as exc:
        st.error(f"Failed to save queue: {exc}")
        return False


def update_queue_position(
    client: Client, annotator_uid: str, dataset_hash: str, position: int
) -> None:
    try:  # pragma: no cover - requires Supabase
        (
            client.table("annotator_queues")
            .update({"position": int(position)})
            .eq("annotator_uid", annotator_uid)
            .eq("dataset_hash", dataset_hash)
            .execute()
        )
    except Exception as exc:
        st.error(f"Failed to update queue position: {exc}")


def build_photo_first_order(
    rows: List[Dict[str, Any]], annotator_uid: str
) -> List[str]:
    photo_ids = [str(r.get("request_id")) for r in rows if r.get("has_photo")]
    nophoto_ids = [str(r.get("request_id")) for r in rows if not r.get("has_photo")]
    # Deterministic per-user shuffle
    seed_bytes = hashlib.sha256(f"queue:{annotator_uid}".encode("utf-8")).digest()
    seed_int = int.from_bytes(seed_bytes[:8], "big")
    rng = random.Random(seed_int)
    rng.shuffle(photo_ids)
    rng.shuffle(nophoto_ids)
    return photo_ids + nophoto_ids


def passes_minimal_filters(
    record: Dict[str, Any],
    labels: List[Dict[str, Any]],
    *,
    has_photo: Optional[bool],
    status_filter: str,
    annotator_uid: str,
    max_annotators: int,
    case_status: Optional[str] = None,
    goa_only: bool = False,
) -> bool:
    if has_photo is not None and bool(record.get("has_photo")) != has_photo:
        return False
    if case_status is not None:
        status_lower = str(record.get("status") or "").strip().lower()
        closed_states = {"closed", "completed", "resolved"}
        is_closed = status_lower in closed_states
        if case_status == "open" and is_closed:
            return False
        if case_status == "closed" and not is_closed:
            return False
    if goa_only:
        suggested, _ = suggest_outcome(record)
        if suggested != "unable_to_locate":
            return False
    annotators = unique_annotators(labels)
    mine = annotator_uid in annotators
    if status_filter == "unlabeled" and mine:
        return False
    if status_filter == "labeled" and not mine:
        return False
    # Skip if already at cap and not mine
    if not mine and len(annotators) >= max_annotators:
        return False
    return True


def status_badge(record: Dict[str, Any]) -> str:
    status_raw = str(record.get("status") or "Unknown").strip()
    closed_states = {"closed", "completed", "resolved"}
    if status_raw.lower() in closed_states:
        return f"🔴 {status_raw.title()}"
    if status_raw.lower() in {"open", "assigned", "in progress"}:
        return f"🟢 {status_raw.title()}"
    return f"🟡 {status_raw.title()}"


def outcome_highlight(
    record: Dict[str, Any], dataset_cutoff: Optional[datetime] = None
) -> str:
    status_raw = str(record.get("status") or "Status unknown").strip()
    status_lower = status_raw.lower()
    closed_states = {"closed", "completed", "resolved"}
    parts: List[str] = []
    if status_raw:
        parts.append(status_raw.title())

    # Show time to resolution for closed, time elapsed for open
    if status_lower in closed_states:
        duration = record.get("hours_to_resolution")
        if duration is not None:
            parts.append(f"after {format_duration_hours(duration)}")
    else:
        if dataset_cutoff is not None:
            created_dt = parse_created_at(record.get("created_at"))
            if created_dt is not None:
                elapsed_hours = max(
                    0.0, (dataset_cutoff - created_dt).total_seconds() / 3600.0
                )
                parts.append(f"for {format_duration_hours(elapsed_hours)}")

    summary = " ".join(parts) if parts else "Status unknown"
    notes = record.get("status_notes") or record.get("resolution_notes")
    if notes:
        trimmed = notes.strip().replace("\n", " ")
        if len(trimmed) > 120:
            trimmed = trimmed[:117].rstrip() + "…"
        summary += f" → {trimmed}"
    return summary


def record_feature_defaults(record: Dict[str, Any]) -> Dict[str, Any]:
    def bool_default(value: Any) -> Optional[bool]:
        if value is None:
            return None
        return bool(value)

    defaults: Dict[str, Any] = {
        "lying_face_down": record.get("tag_lying_face_down") is True,
        "safety_issue": record.get("tag_safety_issue") is True,
        "drugs": record.get("tag_drugs") is True,
        "blocking": bool_default(record.get("kw_blocking")),
        "on_ramp": bool_default(record.get("kw_onramp")),
        "propane_or_flame": bool_default(record.get("kw_propane"))
        or bool_default(record.get("kw_fire")),
        "children_present": bool_default(record.get("kw_children")),
        "wheelchair": bool_default(record.get("kw_wheelchair")),
    }

    num_people = record.get("tag_num_people")
    if isinstance(num_people, (int, float)):
        if num_people <= 0:
            defaults["num_people_bin"] = "0"
        elif num_people <= 1:
            defaults["num_people_bin"] = "1"
        elif num_people <= 3:
            defaults["num_people_bin"] = "2-3"
        elif num_people <= 5:
            defaults["num_people_bin"] = "4-5"
        else:
            defaults["num_people_bin"] = "6+"

    size_feet = record.get("tag_size_feet")
    if isinstance(size_feet, (int, float)):
        if size_feet <= 0:
            defaults["size_feet_bin"] = "0"
        elif size_feet <= 20:
            defaults["size_feet_bin"] = "1-20"
        elif size_feet <= 80:
            defaults["size_feet_bin"] = "21-80"
        elif size_feet <= 150:
            defaults["size_feet_bin"] = "81-150"
        else:
            defaults["size_feet_bin"] = "150+"

    tents_value = record.get("tag_tents_present")
    if isinstance(tents_value, (int, float)):
        defaults["tents_count"] = max(int(tents_value), 0)
    elif isinstance(tents_value, bool):
        defaults["tents_count"] = 1 if tents_value else 0

    return {k: v for k, v in defaults.items() if v is not None}


def subset(
    rows: List[Dict[str, Any]],
    *,
    has_photo: Optional[bool],
    kw_filters: List[str],
    tag_filters: List[str],
    status_filter: str,
    labels_by_request: Dict[str, List[Dict[str, Any]]],
    annotator_uid: str,
    search_text: str = "",
    only_mine: bool = False,
    require_rich_context: bool = False,
) -> List[Dict[str, Any]]:
    def pass_kw(r: Dict[str, Any]) -> bool:
        return all(r.get(f"kw_{k}", False) for k in kw_filters)

    def pass_tag(r: Dict[str, Any]) -> bool:
        for t in tag_filters:
            if t == "lying_face_down" and r.get("tag_lying_face_down") is not True:
                return False
            if t == "tents_present" and r.get("tag_tents_present") is not True:
                return False
        return True

    desired = status_filter
    search_text = search_text.strip().lower()
    out_rows: List[Dict[str, Any]] = []
    for r in rows:
        if has_photo is not None and bool(r.get("has_photo")) != has_photo:
            continue
        if not pass_kw(r):
            continue
        if not pass_tag(r):
            continue
        rid = str(r.get("request_id"))
        labels = labels_by_request.get(rid, [])
        req_status = request_status(labels, REQUIRED_UNIQUE_FOR_COMPLETION)
        if desired != "all" and req_status != desired:
            if desired == "unlabeled" and req_status == "unlabeled":
                pass
            else:
                continue
        if search_text:
            haystack_parts = [
                rid,
                str(r.get("text") or ""),
                str(r.get("status_notes") or ""),
                str(r.get("service_subtype") or ""),
            ]
            keywords = [k for k in r.keys() if k.startswith("kw_") and r.get(k)]
            haystack_parts.extend(keywords)
            haystack_parts.append(str(r.get("created_at") or ""))
            haystack_parts.append(" ".join(str(l.get("notes") or "") for l in labels))
            haystack = " ".join(haystack_parts).lower()
            if search_text not in haystack:
                continue
        if require_rich_context:
            has_context = bool(
                r.get("has_photo") or r.get("status_notes") or r.get("resolution_notes")
            )
            if not has_context:
                continue
        if only_mine and annotator_uid not in unique_annotators(labels):
            continue
        if not can_annotator_label(labels, annotator_uid, MAX_ANNOTATORS):
            continue
        out_rows.append(r)
    return out_rows


def main() -> None:
    supabase_client = get_supabase_client()
    if supabase_client is None:
        st.stop()

    if not st.user.is_logged_in:
        st.title(APP_TITLE)
        st.caption("Log in with Auth0 to continue.")
        st.button("Log in", type="primary", on_click=st.login)
        st.stop()

    session_user = st.user
    raw_email = (getattr(session_user, "email", "") or "").strip()
    user_email = raw_email.lower()
    identity_source = (
        getattr(session_user, "id", None)
        or raw_email
        or getattr(session_user, "username", None)
        or getattr(session_user, "name", None)
        or str(uuid.uuid4())
    )
    annotator_uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"auth:{identity_source}"))
    annotator_display = (
        getattr(session_user, "name", None) or raw_email or annotator_uid
    )
    annotator_role = "reviewer"

    NOTE_STATE_KEY = "note_text"
    NOTE_REQ_KEY = "note_text_request_id"

    def reset_note_state() -> None:
        st.session_state.pop(NOTE_STATE_KEY, None)
        st.session_state.pop(NOTE_REQ_KEY, None)

    identity_parts = [annotator_display]
    if raw_email and raw_email.lower() != annotator_display.lower():
        identity_parts.append(raw_email)
    st.sidebar.caption(f"Signed in as {' · '.join(identity_parts)}")

    undo_context = st.session_state.get("undo_context")
    if undo_context:
        undo_container = st.container()
        with undo_container:
            msg_col, undo_col, dismiss_col = st.columns([6, 1.8, 1.2])
            with msg_col:
                st.success(
                    f"Saved request {undo_context['request_id']}.",
                    icon="✅",
                )
            with undo_col:
                if st.button(
                    "Undo last save",
                    key="undo_last_save",
                    help="Removes the most recent label you saved and returns you to that request.",
                ):
                    label_id_to_delete = undo_context.get("label_id")
                    if label_id_to_delete and delete_label(
                        label_id_to_delete, supabase_client
                    ):
                        previous_id = undo_context.get("request_id")
                        if previous_id:
                            # Mark a navigation intent back to the undone item; handled after queue is built
                            st.session_state["undo_jump_request_id"] = str(previous_id)
                    previous_prefill = undo_context.get("previous_prefill")
                    if previous_prefill:
                        st.session_state["prefill"] = previous_prefill
                        request_ref = undo_context.get("request_id", "")
                        st.session_state["queue_search"] = str(request_ref)
                        st.session_state.pop("undo_context", None)
                        st.rerun()
            with dismiss_col:
                if st.button(
                    "Dismiss",
                    key="dismiss_save_notice",
                    help="Hide this message without undoing the label.",
                ):
                    st.session_state.pop("undo_context", None)
                    st.rerun()

    if st.sidebar.button("Log out"):
        st.logout()
        st.session_state.pop("undo_context", None)
        st.session_state.pop("prefill", None)
        st.session_state.pop("current_request_id", None)
        st.session_state.pop("pending_request_id", None)
        st.session_state.pop("queue_signature", None)
        st.rerun()

    rows_all = load_rows(RAW)
    dataset_cutoff = compute_dataset_cutoff(rows_all)
    labels_by_request = load_labels_supabase(supabase_client)

    my_labels: List[Dict[str, Any]] = []
    for rid, entries in labels_by_request.items():
        for entry in entries:
            annot_id = (
                entry.get("annotator_uid")
                or entry.get("annotator")
                or entry.get("annotator_email")
            )
            if str(annot_id) != annotator_uid:
                continue
            my_labels.append(
                {
                    "request_id": str(rid),
                    "timestamp": entry.get("timestamp"),
                    "priority": entry.get("priority"),
                    "outcome_alignment": entry.get("outcome_alignment"),
                    "follow_up_need": entry.get("follow_up_need"),
                    "raw": entry,
                }
            )

    my_labels.sort(
        key=lambda item: parse_iso(item.get("timestamp")) or datetime.min, reverse=True
    )

    status_counts: Dict[str, int] = {}
    status_by_request: Dict[str, str] = {}
    for record in rows_all:
        rid = str(record.get("request_id"))
        labels = labels_by_request.get(rid, [])
        status = request_status(labels, REQUIRED_UNIQUE_FOR_COMPLETION)
        status_by_request[rid] = status
        status_counts[status] = status_counts.get(status, 0) + 1
    with_images = sum(1 for r in rows_all if r.get("has_photo"))
    with_notes = sum(1 for r in rows_all if r.get("status_notes"))

    if BACKUP_SETTING is None:
        enable_file_backup = False
    else:
        enable_file_backup = BACKUP_SETTING not in {"0", "false", "False"}

    st.sidebar.header("Queue filters")
    with st.sidebar.expander("Queue snapshot", expanded=False):
        st.write(f"Total requests: {len(rows_all)}")
        for key in ["unlabeled", "labeled"]:
            st.write(f"{key.replace('_', ' ').title()}: {status_counts.get(key, 0)}")
        st.write("—")
        st.write(f"With photos: {with_images}")
        if dataset_cutoff is not None:
            st.write(f"Dataset cutoff: {dataset_cutoff.isoformat()}")

    with st.sidebar.expander("My recent labels", expanded=False):
        if my_labels:
            preview_rows = []
            option_map: Dict[str, Dict[str, Any]] = {}
            for entry in my_labels[:20]:
                formatted_time = format_timestamp(entry.get("timestamp"))
                preview_rows.append(
                    {
                        "Request": entry["request_id"],
                        "Priority": entry.get("priority") or "—",
                        "Outcome": outcome_display(entry.get("outcome_alignment")),
                        "Follow-up": follow_up_display(entry.get("follow_up_need")),
                        "Saved": formatted_time,
                    }
                )
                label_text = f"{entry['request_id']} · {entry.get('priority') or '—'} · {formatted_time}"
                option_map[label_text] = entry

                st.dataframe(
                    pd.DataFrame(preview_rows), width="stretch", hide_index=True
                )

            select_choices = ["None"] + list(option_map.keys())
            selected_option = st.selectbox("Jump to request", select_choices)
            if selected_option != "None" and st.button(
                "Load selected request",
                key="load_my_label",
                help="Jump to the selected request and preload your last label.",
            ):
                entry = option_map[selected_option]
                label_prefill = entry.get("raw") or {}
                st.session_state["prefill"] = deepcopy(label_prefill)
                st.session_state["queue_search"] = str(entry["request_id"])
                st.session_state["current_request_id"] = str(entry["request_id"])
                st.session_state["status_filter"] = "all"
                st.session_state.pop("undo_context", None)
                st.session_state["reset"] = True
                st.rerun()
        else:
            st.caption("No saved labels yet.")

    has_photo = st.sidebar.selectbox(
        "Photo status", ["any", "with photos", "no photos"], key="photo_status"
    )
    has_photo = (
        None if has_photo == "any" else (True if has_photo == "with photos" else False)
    )
    case_status = st.sidebar.selectbox(
        "Case status", ["any", "open only", "closed only"], index=0, key="case_status"
    )
    case_status_value: Optional[str] = None
    if case_status == "open only":
        case_status_value = "open"
    elif case_status == "closed only":
        case_status_value = "closed"
    goa_only = st.sidebar.checkbox(
        "Likely GOA (auto-suggested)",
        value=False,
        help="Include only cases whose notes/status suggest unable to locate / gone on arrival.",
        key="goa_only",
    )
    status_default = st.session_state.get("status_filter", STATUS_FILTER_OPTIONS[0])
    if status_default not in STATUS_FILTER_OPTIONS:
        status_default = STATUS_FILTER_OPTIONS[0]
    status_filter = st.sidebar.selectbox(
        "Request status",
        STATUS_FILTER_OPTIONS,
        index=STATUS_FILTER_OPTIONS.index(status_default),
    )
    st.session_state["status_filter"] = status_filter
    # Remove advanced filters to keep UX simple
    go_home_cols = st.sidebar.columns([1, 1])
    with go_home_cols[0]:
        if st.button("Reset filters"):
            st.session_state["status_filter"] = STATUS_FILTER_OPTIONS[0]
            st.session_state["photo_status"] = "any"
            st.session_state["case_status"] = "any"
            st.session_state["goa_only"] = False
            st.session_state["reset"] = True
            st.session_state["reset_to_first"] = True
    with go_home_cols[1]:
        if st.button("Go to start"):
            # Force base position to 0 and reset
            try:
                update_queue_position(supabase_client, annotator_uid, dataset_hash, 0)  # type: ignore[arg-type]
            except Exception:
                pass
            st.session_state["reset"] = True
            st.session_state["reset_to_first"] = True

    st.sidebar.caption(
        "Hotkeys: Save ↦ Ctrl/Cmd+Enter • Prev ↦ Shift/Alt+Left • Skip ↦ Shift/Alt+Right"
    )

    queue_signature = json.dumps(
        {
            "has_photo": has_photo,
            "status": status_filter,
            "case_status": case_status_value,
            "goa_only": bool(goa_only),
        },
        sort_keys=True,
    )

    # Build or load per-user queue (photo-first, stable). Do not reorder per interaction.
    dataset_hash = compute_dataset_hash(RAW)
    saved = load_saved_queue(supabase_client, annotator_uid, dataset_hash)
    if saved is None:
        base_order = build_photo_first_order(rows_all, annotator_uid)
        save_queue(supabase_client, annotator_uid, dataset_hash, base_order, 0)
        saved = {"queue": base_order, "position": 0}
    base_queue_ids: List[str] = list(saved.get("queue") or [])
    position = int(saved.get("position") or 0)
    base_index_by_id: Dict[str, int] = {rid: i for i, rid in enumerate(base_queue_ids)}

    # Construct filtered working set without changing base order
    rows_by_id_all: Dict[str, Dict[str, Any]] = {
        str(r.get("request_id")): r for r in rows_all
    }
    working_ids: List[str] = []
    for rid in base_queue_ids:
        rec = rows_by_id_all.get(str(rid))
        if not rec:
            continue
        labels = labels_by_request.get(str(rid), [])
        if passes_minimal_filters(
            rec,
            labels,
            has_photo=has_photo,
            status_filter=status_filter,
            annotator_uid=annotator_uid,
            max_annotators=MAX_ANNOTATORS,
            case_status=case_status_value,
            goa_only=bool(goa_only),
        ):
            working_ids.append(str(rid))

    if not working_ids:
        st.warning("No items matching the filters. Adjust the sidebar.")
        st.stop()

    rows = [rows_by_id_all[rid] for rid in working_ids]
    # Map saved base position to the first working item at or after it
    preferred_pos = 0
    try:
        candidates = [
            i
            for i, rid in enumerate(working_ids)
            if base_index_by_id.get(rid, -1) >= position
        ]
        if candidates:
            preferred_pos = candidates[0]
        else:
            preferred_pos = 0
    except Exception:
        preferred_pos = 0

    def recommended_key(record: Dict[str, Any]) -> Tuple[Any, ...]:
        rid = str(record.get("request_id"))
        status = status_by_request.get(rid, "labeled")
        # With review mode removed, prioritize unlabeled first, then others by context/recency
        status_priority = {
            "unlabeled": 0,
            "needs_review": 1,
            "labeled": 2,
        }
        status_score = status_priority.get(status, 4)
        photo_score = 0 if record.get("has_photo") else 1
        context_score = -rich_context_score(record)
        recency_score = parse_created_at(record.get("created_at")) or datetime.max
        user_random = user_random_value(rid, annotator_uid)
        return (status_score, photo_score, context_score, recency_score, user_random)

    # No per-interaction sorting; order is stable based on per-user queue

    if not rows:
        st.warning("No items matching the filters. Adjust the sidebar.")
        st.stop()

    request_ids: List[str] = [str(r.get("request_id")) for r in rows]
    rows_by_id: Dict[str, Dict[str, Any]] = {str(r.get("request_id")): r for r in rows}

    reset_requested = st.session_state.pop("reset", False)

    # Stable queue: freeze IDs and track position for deterministic navigation
    if reset_requested or st.session_state.get("queue_signature") != queue_signature:
        st.session_state["queue_signature"] = queue_signature
        st.session_state["queue_ids"] = request_ids[:]
        if st.session_state.pop("reset_to_first", False):
            st.session_state["queue_pos"] = 0
        else:
            st.session_state["queue_pos"] = preferred_pos

    # Prune queue_ids to currently visible rows (in case filters changed externally without signature change)
    queue_ids: List[str] = [
        rid for rid in st.session_state.get("queue_ids", []) if rid in rows_by_id
    ]
    if not queue_ids:
        st.warning("No items matching the filters. Adjust the sidebar.")
        st.stop()
    st.session_state["queue_ids"] = queue_ids

    pos = int(st.session_state.get("queue_pos", 0))
    if pos < 0 or pos >= len(queue_ids):
        pos = 0
    st.session_state["queue_pos"] = pos

    # If there is an undo jump requested, navigate to that request's index in the frozen queue
    undo_jump_id = st.session_state.pop("undo_jump_request_id", None)
    if undo_jump_id and undo_jump_id in queue_ids:
        pos = queue_ids.index(undo_jump_id)
        st.session_state["queue_pos"] = pos

    current_id = queue_ids[pos]
    st.session_state["current_request_id"] = current_id
    idx = pos
    queue_size = len(queue_ids)

    record = rows_by_id[current_id]
    req_id = current_id
    existing_labels = sort_labels(labels_by_request.get(req_id, []))
    latest_other_label = latest_label_excluding(existing_labels, annotator_uid)
    review_mode = False
    current_status = request_status(existing_labels, REQUIRED_UNIQUE_FOR_COMPLETION)

    def widget_key(name: str) -> str:
        return f"{req_id}_{name}"

    prev_clicked = False
    save_clicked = False
    skip_clicked = False

    # Count of unique requests labeled by current user (for privacy-forward UI)
    my_labeled_count = len({item["request_id"] for item in my_labels})

    summary_col, action_col = st.columns([5, 2], gap="small")
    with summary_col:
        status_str = str(record.get("status") or "").strip().lower()
        is_closed = status_str in {"closed", "completed", "resolved"}
        time_label = "Time to resolution" if is_closed else "Time elapsed"
        time_value = "—"
        if is_closed:
            time_value = format_duration_hours(record.get("hours_to_resolution"))
        else:
            created_dt = parse_created_at(record.get("created_at"))
            if created_dt is not None and dataset_cutoff is not None:
                elapsed_hours = max(
                    0.0, (dataset_cutoff - created_dt).total_seconds() / 3600.0
                )
                time_value = format_duration_hours(elapsed_hours)
        st.caption(
            f"Queue {queue_size} · My labeled {my_labeled_count} · "
            f"Index {idx + 1}/{queue_size} · {time_label} {time_value}"
        )
        st.markdown(
            f"**Case snapshot:** {status_badge(record)} · {outcome_highlight(record, dataset_cutoff)}"
        )

    with action_col:
        btn_prev, btn_save, btn_skip = st.columns([1, 1.3, 1])

        with btn_prev:
            if keyboard_button(
                "Prev",
                shortcuts=["shift+left", "alt+left"],
                width="stretch",
                key="nav-prev",
            ):
                prev_clicked = True

        with btn_save:
            if keyboard_button(
                "Save & Next",
                shortcuts=["ctrl+enter", "cmd+enter"],
                button_type="primary",
                width="stretch",
                key="nav-save",
            ):
                save_clicked = True

        with btn_skip:
            if keyboard_button(
                "Skip",
                shortcuts=["shift+right", "alt+right"],
                width="stretch",
                key="nav-skip",
            ):
                skip_clicked = True

    images = resolve_images(record)

    left, right = st.columns([5, 4], gap="medium")
    with left:
        st.subheader(f"Request {req_id or '—'}")
        st.caption(
            " | ".join(
                [
                    f"Created: {format_timestamp(record.get('created_at'))}",
                    f"Updated: {format_timestamp(record.get('updated_at'))}",
                    f"District: {record.get('police_district') or '—'}",
                ]
            )
        )
        st.write(record.get("text") or "(No description)")

        status_notes = record.get("status_notes")
        resolution_notes = record.get("resolution_notes")
        after_action_url = record.get("after_action_url")
        if status_notes or resolution_notes or after_action_url:
            if status_notes:
                st.markdown("**Closure notes:**")
                st.write(status_notes)
            if resolution_notes:
                st.markdown("**Post-closure notes:**")
                st.write(resolution_notes)
            if after_action_url:
                st.markdown("**After action link:**")
                st.markdown(f"[Open after action report]({after_action_url})")

        st.markdown("---")
        st.markdown("#### Images")
        if not images:
            st.info("No images for this report.")
        else:
            image_index_state_key = widget_key("image_index")
            num_images = len(images)
            stored_index = st.session_state.get(image_index_state_key, 0)
            if stored_index >= num_images:
                stored_index = 0
                st.session_state[image_index_state_key] = stored_index

            if num_images > 1:
                captions: List[str] = []
                for img_idx, info in enumerate(images):
                    details: List[str] = []
                    if info.get("status") and info.get("status") != "ok":
                        details.append(str(info.get("status")))
                    label = f"Photo {img_idx + 1} of {num_images}"
                    if details:
                        label = f"{label} ({'; '.join(details)})"
                    captions.append(label)

                st.caption(
                    "Review each photo using the buttons or dropdown below. The selected image fills the panel."
                )

                nav_cols = st.columns([1, 3, 1])
                with nav_cols[0]:
                    if st.button(
                        "◀ Previous photo",
                        key=widget_key("image_prev"),
                        help="Show the previous photo",
                    ):
                        st.session_state[image_index_state_key] = (
                            stored_index - 1
                        ) % num_images
                        st.rerun()

                with nav_cols[1]:
                    selected_index = st.selectbox(
                        "Photo selector",
                        options=list(range(num_images)),
                        index=stored_index,
                        format_func=lambda i: captions[i],
                        key=widget_key("image_select"),
                    )
                    if selected_index != stored_index:
                        st.session_state[image_index_state_key] = selected_index
                        stored_index = selected_index

                with nav_cols[2]:
                    if st.button(
                        "Next photo ▶",
                        key=widget_key("image_next"),
                        help="Show the next photo",
                    ):
                        st.session_state[image_index_state_key] = (
                            stored_index + 1
                        ) % num_images
                        st.rerun()

                current_index = st.session_state.get(image_index_state_key, 0)
                current_index = max(0, min(current_index, num_images - 1))
            else:
                st.caption("Single photo provided for this request.")
                current_index = 0
                st.session_state[image_index_state_key] = 0
            current_info = images[current_index]
            image_source = current_info.get("local_path") or current_info.get("url")
            image_caption_parts: List[str] = []
            if current_info.get("status") and current_info.get("status") != "ok":
                image_caption_parts.append(str(current_info.get("status")))
            if current_info.get("local_path"):
                image_caption_parts.append("Cached locally")
            image_caption_parts.append(
                f"Viewing photo {current_index + 1} of {num_images}"
            )
            if image_source:
                st.image(image_source, width="stretch")
            else:
                st.warning("This photo could not be loaded.")
            st.caption(" · ".join(image_caption_parts))

    with right:
        # Review banner removed

        glossary_pairs = list(FIELD_GLOSSARY.items())
        if hasattr(st, "popover"):
            with st.popover("ℹ️ Field glossary"):
                for label, desc in glossary_pairs:
                    st.markdown(f"**{label}** — {desc}")
        else:
            with st.expander("Field glossary", expanded=False):
                for label, desc in glossary_pairs:
                    st.markdown(f"**{label}** — {desc}")

        latest_for_user = latest_label_for_annotator(existing_labels, annotator_uid)
        prefill_candidate = st.session_state.pop("prefill", latest_for_user)
        prefill = prefill_candidate
        prefill_features = coerce_features(prefill) if prefill else {}
        record_defaults = record_feature_defaults(record)
        initial_features = {**record_defaults, **prefill_features}

        prefill_from_self = False
        if prefill:
            uid_match = str(prefill.get("annotator_uid")) == annotator_uid
            email_match = bool(
                raw_email
                and (prefill.get("annotator_email") or "").lower() == user_email
            )
            prefill_from_self = uid_match or email_match

        if "tents_count" not in initial_features:
            legacy_tents = prefill_features.get("tents_present")
            if isinstance(legacy_tents, bool):
                initial_features["tents_count"] = 1 if legacy_tents else 0
        initial_features.pop("tents_present", None)

        def prefill_bool(key: str) -> bool:
            return bool(initial_features.get(key, False))

        def prefill_select(key: str, fallback: str, options: List[str]) -> int:
            value = initial_features.get(key, fallback)
            if value not in options:
                value = fallback
            return options.index(value)

        def prefill_list(key: str) -> List[str]:
            raw_value = initial_features.get(key)
            if isinstance(raw_value, list):
                return [str(item) for item in raw_value]
            if raw_value is None:
                return []
            if isinstance(raw_value, tuple):
                return [str(item) for item in raw_value]
            if isinstance(raw_value, str) and raw_value:
                return [raw_value]
            return []

        def prefill_int(key: str, fallback: int = 0) -> int:
            raw_value = initial_features.get(key, fallback)
            try:
                return int(raw_value)
            except (TypeError, ValueError):
                return fallback

        def resolve_priority_label(prefill_value: Optional[Any]) -> str:
            if prefill_value is None:
                return "Medium"
            value_str = str(prefill_value).strip()
            if value_str:
                lowered = value_str.lower()
                if lowered in PRIORITY_STORAGE.values():
                    return value_str.title()
                if lowered in PRIORITY_LEGACY_MAP:
                    return PRIORITY_LEGACY_MAP[lowered]
            return "Medium"

        priority_label_default = resolve_priority_label(
            prefill.get("priority") if prefill else None
        )
        priority_index = PRIORITY_OPTIONS.index(priority_label_default)

        review_status_default = "pending"

        if prefill_from_self and prefill:
            review_notes_default = prefill.get("review_notes") or ""
        else:
            review_notes_default = ""

        st.markdown("### 1. Priority & review")
        priority_label = st.radio(
            "Priority",
            PRIORITY_OPTIONS,
            horizontal=True,
            index=priority_index,
            help=LABEL_TIPS["priority"],
            key=widget_key("priority"),
        )
        priority_explanation = st.text_input(
            "Priority rationale",
            value=str(
                (prefill or {}).get("features", {}).get("priority_explanation") or ""
            ),
            help="Briefly explain why you chose this priority.",
            key=widget_key("priority_reason"),
        )
        goa_values = [value for value, _ in GOA_WINDOW_OPTIONS]
        goa_labels = [label for _, label in GOA_WINDOW_OPTIONS]
        goa_default = resolve_goa_window(initial_features)
        goa_default_index = (
            goa_values.index(goa_default) if goa_default in goa_values else 0
        )
        goa_selected_label = st.selectbox(
            "GOA expectation",
            goa_labels,
            index=goa_default_index,
            help=LABEL_TIPS["goa_window"],
            key=widget_key("goa_window"),
        )
        goa_window_value = goa_values[goa_labels.index(goa_selected_label)]
        goa_explanation = st.text_input(
            "GOA rationale",
            value=str((prefill or {}).get("features", {}).get("goa_explanation") or ""),
            help="Why this GOA window? Mention cues like movement, time of day, etc.",
            key=widget_key("goa_reason"),
        )

        review_status = "pending"

        priority_value = PRIORITY_STORAGE[priority_label]

        st.markdown("### 2. Routing")
        routing_default = initial_features.get("routing_department") or "Unknown"
        if routing_default not in ROUTING_DEPARTMENTS:
            routing_default = "Unknown"
        routing_department = st.selectbox(
            "Routing department",
            ROUTING_DEPARTMENTS,
            index=ROUTING_DEPARTMENTS.index(routing_default),
            help=LABEL_TIPS["routing_department"],
            key=widget_key("routing_department"),
        )
        routing_other_default = str(initial_features.get("routing_other") or "")
        routing_other = ""
        if routing_department == "Other":
            routing_other = st.text_input(
                "Describe routing",
                value=routing_other_default,
                key=widget_key("routing_other"),
            )
        routing_explanation = st.text_input(
            "Routing rationale",
            value=str(
                (prefill or {}).get("features", {}).get("routing_explanation") or ""
            ),
            help="Why this department?",
            key=widget_key("routing_reason"),
        )

        st.markdown("### 3. On-scene observations")
        feature_options = [
            ("lying_face_down", FEATURE_DISPLAY_NAMES["lying_face_down"]),
            ("safety_issue", FEATURE_DISPLAY_NAMES["safety_issue"]),
            ("drugs", FEATURE_DISPLAY_NAMES["drugs"]),
            ("blocking", FEATURE_DISPLAY_NAMES["blocking"]),
            ("on_ramp", FEATURE_DISPLAY_NAMES["on_ramp"]),
            ("propane_or_flame", FEATURE_DISPLAY_NAMES["propane_or_flame"]),
            ("children_present", FEATURE_DISPLAY_NAMES["children_present"]),
            ("wheelchair", FEATURE_DISPLAY_NAMES["wheelchair"]),
        ]
        default_feature_labels = [
            label for key, label in feature_options if prefill_bool(key)
        ]
        selected_feature_labels = st.multiselect(
            "Observed conditions",
            [label for _, label in feature_options],
            default=default_feature_labels,
            help="Select all observed conditions that apply.",
            key=widget_key("observed_features"),
        )
        selected_feature_keys = {
            key for key, label in feature_options if label in selected_feature_labels
        }

        metrics_cols = st.columns([1, 1, 1])
        with metrics_cols[0]:
            tents_count = st.number_input(
                "Tents (count)",
                min_value=0,
                max_value=50,
                step=1,
                value=prefill_int("tents_count", 0),
                help=FEATURE_TIPS["tents_count"],
                key=widget_key("feature_tents_count"),
            )
        with metrics_cols[1]:
            num_people_opts = ["0", "1", "2-3", "4-5", "6+"]
            num_people_bin = st.selectbox(
                FEATURE_DISPLAY_NAMES["num_people_bin"],
                num_people_opts,
                index=prefill_select("num_people_bin", "1", num_people_opts),
                help=FEATURE_TIPS["num_people_bin"],
                key=widget_key("num_people_bin"),
            )
        with metrics_cols[2]:
            size_opts = ["0", "1-20", "21-80", "81-150", "150+"]
            size_feet_bin = st.selectbox(
                FEATURE_DISPLAY_NAMES["size_feet_bin"],
                size_opts,
                index=prefill_select("size_feet_bin", "21-80", size_opts),
                help=FEATURE_TIPS["size_feet_bin"],
                key=widget_key("size_feet_bin"),
            )

        feature_flags = {
            key: key in selected_feature_keys for key, _ in feature_options
        }
        lying = feature_flags["lying_face_down"]
        safety = feature_flags["safety_issue"]
        drugs = feature_flags["drugs"]
        blocking = feature_flags["blocking"]
        onramp = feature_flags["on_ramp"]
        propane = feature_flags["propane_or_flame"]
        kids = feature_flags["children_present"]
        chair = feature_flags["wheelchair"]

        st.markdown("### 4. Notes & evidence")
        notes_val = (
            prefill.get("notes") if prefill_from_self and prefill else ""
        ) or ""
        if st.session_state.get(NOTE_REQ_KEY) != req_id:
            st.session_state[NOTE_REQ_KEY] = req_id
            st.session_state[NOTE_STATE_KEY] = str(notes_val)
        notes = st.text_area(
            "Notes",
            key=NOTE_STATE_KEY,
            height=110,
            help=LABEL_TIPS["notes"],
        )

        review_notes = ""

        info_source_options = [
            "Photos",
            "Description text",
            "Status notes",
            "Resolution notes",
            "Prior labels",
            "Map/location",
            "Other external context",
        ]
        prefill_sources = prefill.get("evidence_sources") if prefill else []
        valid_sources = [s for s in prefill_sources or [] if s in info_source_options]
        info_sources = st.multiselect(
            "Information used",
            info_source_options,
            default=(
                valid_sources or ["Photos"]
                if record.get("has_photo")
                else valid_sources
            ),
            help=LABEL_TIPS["evidence_sources"],
            key=widget_key("info_sources"),
        )

        outcome_values = [opt[0] for opt in OUTCOME_OPTIONS]
        outcome_labels = [opt[1] for opt in OUTCOME_OPTIONS]
        # Suggested outcome from notes/status
        suggested_outcome, suggestion_reason = suggest_outcome(record)
        chosen_outcome_default = ""
        if priority_label == "Invalid":
            chosen_outcome_default = "invalid_report"
        elif suggested_outcome in outcome_values:
            chosen_outcome_default = str(suggested_outcome)
        elif prefill:
            raw_outcome = prefill.get("outcome_alignment")
            if raw_outcome in outcome_values:
                chosen_outcome_default = str(raw_outcome)
        default_outcome_index = outcome_values.index(chosen_outcome_default)
        outcome_label = st.selectbox(
            "Outcome alignment",
            outcome_labels,
            index=default_outcome_index,
            help=(
                LABEL_TIPS["outcome_alignment_open"]
                if str(record.get("status")).strip().lower()
                not in {"closed", "completed", "resolved"}
                else LABEL_TIPS["outcome_alignment"]
            ),
            key=widget_key("outcome_alignment"),
        )
        outcome_alignment = outcome_values[outcome_labels.index(outcome_label)]
        if outcome_alignment == "":
            outcome_alignment = None
        if priority_label == "Invalid":
            st.caption("Suggested from priority: Invalid report / not a 311 issue")
        elif suggested_outcome:
            st.caption(suggestion_reason)

        follow_up_values = [opt[0] for opt in FOLLOW_UP_OPTIONS]
        follow_up_labels = [opt[1] for opt in FOLLOW_UP_OPTIONS]
        prefill_follow: List[str] = []
        if prefill:
            raw_follow = prefill.get("follow_up_need") or []
            if isinstance(raw_follow, str):
                raw_follow = [raw_follow]
            if isinstance(raw_follow, list):
                prefill_follow = [
                    str(item) for item in raw_follow if item in follow_up_values
                ]
        default_follow_labels = [
            follow_up_labels[follow_up_values.index(item)] for item in prefill_follow
        ]
        follow_up_selected_labels = st.multiselect(
            "Follow-up needs",
            follow_up_labels,
            default=default_follow_labels,
            help=LABEL_TIPS["follow_up_need"],
            key=widget_key("follow_up_need"),
        )
        follow_up_need = [
            follow_up_values[follow_up_labels.index(label)]
            for label in follow_up_selected_labels
        ]

        st.divider()
        st.markdown("#### Context & History")
        summary_tab, history_tab, raw_tab = st.tabs(
            ["Summary", "Annotation history", "Raw data"]
        )

        with summary_tab:
            latest_any = existing_labels[-1] if existing_labels else None
            summary_rows: List[Tuple[str, str]] = []
            keywords = [
                k.replace("kw_", "").replace("_", " ")
                for k in record.keys()
                if k.startswith("kw_") and record[k]
            ]
            # Review summary removed

            summary_rows.extend(
                [
                    (
                        "Routing department",
                        (
                            routing_department
                            if routing_department != "Other"
                            else f"Other ({routing_other or 'unspecified'})"
                        ),
                    ),
                    (
                        "GOA expectation",
                        goa_window_label(goa_window_value),
                    ),
                    (
                        "Observed conditions",
                        ", ".join(selected_feature_labels) or "—",
                    ),
                    ("# of tents", str(tents_count)),
                    ("Est. # of people", num_people_bin),
                    ("Est. footprint (feet)", size_feet_bin),
                ]
            )

            if latest_any:
                latest_any_features = coerce_features(latest_any)
                latest_any_goa = resolve_goa_window(latest_any_features)
                latest_observed = [
                    FEATURE_DISPLAY_NAMES.get(key, key.replace("_", " ").title())
                    for key, value in (latest_any_features or {}).items()
                    if isinstance(value, bool)
                    and value
                    and key in FEATURE_DISPLAY_NAMES
                ]
                summary_rows.extend(
                    [
                        (
                            "Latest GOA expectation",
                            goa_window_label(latest_any_goa),
                        ),
                        (
                            "Latest observed",
                            ", ".join(latest_observed) or "—",
                        ),
                        (
                            "Outcome alignment",
                            outcome_display(latest_any.get("outcome_alignment")),
                        ),
                        (
                            "Follow-up needs",
                            follow_up_display(latest_any.get("follow_up_need")),
                        ),
                    ]
                )

            summary_rows.extend(
                [
                    ("Created", format_timestamp(record.get("created_at"))),
                    ("Updated", format_timestamp(record.get("updated_at"))),
                    ("District", record.get("police_district") or "—"),
                    (
                        "Neighborhood",
                        record.get("neighborhoods_sffind_boundaries") or "—",
                    ),
                    ("Service subtype", record.get("service_subtype") or "—"),
                    (
                        "Location",
                        (
                            f"{record.get('lat')}, {record.get('lon')}"
                            if record.get("lat") and record.get("lon")
                            else "—"
                        ),
                    ),
                    ("Keywords", ", ".join(keywords) or "—"),
                ]
            )
            summary_df = pd.DataFrame(summary_rows, columns=["Attribute", "Value"])
            st.dataframe(summary_df, width="stretch", hide_index=True)

            auto_flags = {
                FEATURE_DISPLAY_NAMES["lying_face_down"]: record.get(
                    "tag_lying_face_down"
                ),
                FEATURE_DISPLAY_NAMES["tents_present"]: record.get("tag_tents_present"),
                "Responder count": record.get("tag_num_people"),
                "Responder footprint (ft)": record.get("tag_size_feet"),
                FEATURE_DISPLAY_NAMES["safety_issue"]: record.get("tag_safety_issue"),
                FEATURE_DISPLAY_NAMES["drugs"]: record.get("tag_drugs"),
            }
            auto_df = pd.DataFrame([auto_flags])
            st.caption("Auto-tags from transform")
            st.dataframe(auto_df, width="stretch", hide_index=True)

        with history_tab:
            if existing_labels:
                my_history = [
                    entry
                    for entry in existing_labels
                    if str(
                        entry.get("annotator_uid")
                        or entry.get("annotator")
                        or entry.get("annotator_email")
                    )
                    == annotator_uid
                ]
                if my_history:
                    history_df = pd.DataFrame(
                        [
                            {
                                "timestamp": format_timestamp(entry.get("timestamp")),
                                "annotator": entry.get("annotator_display")
                                or entry.get("annotator")
                                or entry.get("annotator_uid")
                                or "—",
                                "routing": (entry.get("features") or {}).get(
                                    "routing_department"
                                )
                                or "—",
                                "tents": (
                                    str(
                                        (entry.get("features") or {}).get("tents_count")
                                    )
                                    if (entry.get("features") or {}).get("tents_count")
                                    is not None
                                    else "—"
                                ),
                                "observed": ", ".join(
                                    [
                                        FEATURE_DISPLAY_NAMES.get(
                                            key, key.replace("_", " ").title()
                                        )
                                        for key, value in (
                                            (entry.get("features") or {})
                                        ).items()
                                        if isinstance(value, bool)
                                        and value
                                        and key in FEATURE_DISPLAY_NAMES
                                    ]
                                )
                                or "—",
                                "follow_up": follow_up_display(
                                    entry.get("follow_up_need")
                                ),
                                "outcome": outcome_display(
                                    entry.get("outcome_alignment")
                                ),
                            }
                            for entry in my_history
                        ]
                    )

                    st.dataframe(history_df, width="stretch", hide_index=True)
                else:
                    st.info("No labels yet")
            else:
                st.info("No labels yet")

        with raw_tab:
            raw_record_tab, raw_images_tab, raw_labels_tab = st.tabs(
                ["Record", "Images", "Labels"]
            )
            with raw_record_tab:
                st.json(record)
            with raw_images_tab:
                st.json(images)
            with raw_labels_tab:
                st.json(
                    [
                        entry
                        for entry in existing_labels
                        if str(
                            entry.get("annotator_uid")
                            or entry.get("annotator")
                            or entry.get("annotator_email")
                        )
                        == annotator_uid
                    ]
                )

    col_prev, col_save, col_skip = st.columns([1, 1, 1])

    with col_prev:
        if keyboard_button(
            "Prev",
            shortcuts=["shift+left", "alt+left"],
            width="stretch",
            key=widget_key("nav-prev-bottom"),
        ):
            prev_clicked = True

    with col_save:
        if keyboard_button(
            "Save & Next",
            shortcuts=["ctrl+enter", "cmd+enter"],
            button_type="primary",
            width="stretch",
            key=widget_key("nav-save-bottom"),
        ):
            save_clicked = True

    with col_skip:
        if keyboard_button(
            "Skip",
            shortcuts=["shift+right", "alt+right"],
            width="stretch",
            key=widget_key("nav-skip-bottom"),
        ):
            skip_clicked = True

    if prev_clicked:
        st.session_state["queue_pos"] = (idx - 1) % queue_size
        try:
            # Move base position backward to the previous working item's base index
            prev_working_index = (idx - 1) % queue_size
            prev_rid = working_ids[prev_working_index]
            prev_base_index = base_index_by_id.get(prev_rid, position)
            update_queue_position(
                supabase_client,
                annotator_uid,
                dataset_hash,
                max(prev_base_index, 0),
            )  # type: ignore[arg-type]
        except Exception:
            pass
        reset_note_state()
        st.rerun()
    elif save_clicked:
        # Re-check annotator cap before saving; skip if already at cap and not mine
        existing_annotators = unique_annotators(existing_labels)
        if (
            annotator_uid not in existing_annotators
            and len(existing_annotators) >= MAX_ANNOTATORS
        ):
            st.info("This request already has enough labels; advancing to next.")
            st.session_state["queue_pos"] = (idx + 1) % queue_size
            try:
                update_queue_position(supabase_client, annotator_uid, dataset_hash, st.session_state["queue_pos"])  # type: ignore[arg-type]
            except Exception:
                pass
            reset_note_state()
            st.rerun()
        # Optional: could enforce stricter cap in DB; user opted not to enforce here.
        label_id = str(uuid.uuid4())
        payload = {
            "label_id": label_id,
            "request_id": req_id,
            "annotator_uid": annotator_uid,
            "annotator": annotator_display,
            "annotator_display": annotator_display,
            "role": annotator_role,
            "timestamp": datetime.utcnow().isoformat(),
            "priority": priority_value,
            "features": {
                "lying_face_down": lying,
                "safety_issue": safety,
                "drugs": drugs,
                "blocking": blocking,
                "on_ramp": onramp,
                "propane_or_flame": propane,
                "children_present": kids,
                "wheelchair": chair,
                "num_people_bin": num_people_bin,
                "size_feet_bin": size_feet_bin,
                "tents_count": int(tents_count),
                "goa_window": goa_window_value,
                "priority_explanation": (priority_explanation or None),
                "goa_explanation": (goa_explanation or None),
                "routing_department": routing_department,
                "routing_other": routing_other.strip() or None,
                "routing_explanation": (routing_explanation or None),
            },
            "notes": notes.strip() or None,
            "evidence_sources": info_sources,
            "outcome_alignment": outcome_alignment,
            "follow_up_need": follow_up_need,
            "image_paths": record.get("image_paths"),
            "image_checksums": record.get("image_checksums"),
            "revision_of": prefill.get("label_id") if prefill else None,
            "review_status": "pending",
            "review_notes": None,
        }
        if save_label(payload, supabase_client, enable_file_backup):
            st.session_state["undo_context"] = {
                "label_id": label_id,
                "request_id": req_id,
                "previous_idx": idx,
                "previous_prefill": deepcopy(prefill) if prefill else None,
                "timestamp": datetime.utcnow().isoformat(),
            }
            st.session_state["queue_pos"] = (idx + 1) % queue_size
            # Persist base position: advance to next base index of the selected working item
            try:
                current_rid = working_ids[idx]
                current_base_index = base_index_by_id.get(current_rid, position)
                update_queue_position(
                    supabase_client,
                    annotator_uid,
                    dataset_hash,
                    max(current_base_index + 1, position),
                )  # type: ignore[arg-type]
            except Exception:
                pass
            reset_note_state()
            st.rerun()
        else:
            st.warning(
                "Label not saved due to Supabase error — please fix the issue above and try again.",
                icon="⚠️",
            )
    elif skip_clicked:
        st.session_state["queue_pos"] = (idx + 1) % queue_size
        try:
            current_rid = working_ids[idx]
            current_base_index = base_index_by_id.get(current_rid, position)
            update_queue_position(
                supabase_client,
                annotator_uid,
                dataset_hash,
                max(current_base_index + 1, position),
            )  # type: ignore[arg-type]
        except Exception:
            pass
        reset_note_state()
        st.rerun()


if __name__ == "__main__":
    main()
