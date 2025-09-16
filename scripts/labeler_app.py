#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import random
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import pandas as pd
import streamlit as st

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
    parse_iso,
    request_status,
    sort_labels,
    unique_annotators,
    verify_password,
)

DATA = Path(os.environ.get("LABELER_DATA_DIR", "data"))
RAW = DATA / "transformed.jsonl"
LABELS_DIR = Path(os.environ.get("LABELS_OUTPUT_DIR", DATA / "labels"))
USERS_FILE = Path(os.environ.get("LABELER_USERS_FILE", "config/annotators.json"))
MAX_ANNOTATORS = int(os.environ.get("MAX_ANNOTATORS_PER_REQUEST", "3"))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
)
BACKUP_SETTING = os.environ.get("LABELS_JSONL_BACKUP")

st.set_page_config(page_title="SF311 Priority Labeler", layout="wide")
st.title("SF311 Priority Labeler — Human-in-the-Loop")

ShortcutType = Union[str, Sequence[str]]

FEATURE_TIPS: Dict[str, str] = {
    "lying_face_down": "Report indicates someone is prone or face down on the ground.",
    "safety_issue": "Flags imminent safety hazards (traffic, violence, weapons).",
    "drugs": "Evidence of drug use or paraphernalia on scene.",
    "tents_present": "Tents, makeshift shelters, or similar structures present.",
    "blocking": "Belongings or people are obstructing the right of way (sidewalk/road).",
    "on_ramp": "Located on or immediately adjacent to a freeway on/off ramp.",
    "propane_or_flame": "Propane tanks, open flames, or generators noted.",
    "children_present": "Children observed at the scene.",
    "wheelchair": "Wheelchair or mobility device mentioned in the request.",
    "num_people_bin": "Responder estimate of individuals present (HSOC tag or annotator update).",
    "size_feet_bin": "Linear footprint in feet from HSOC responders. Use bins to adjust if photos show otherwise.",
}

LABEL_TIPS: Dict[str, str] = {
    "priority": "P1 = life-threatening/immediate, P4 = informational only. Priorities should track urgency and resource need.",
    "confidence": "How certain you are about the assigned priority/features based on available evidence.",
    "evidence_sources": "Select the sources you relied on (photos, notes, prior history, etc.).",
    "notes": "Capture rationale, escalation paths, or anomalies for reviewers.",
    "abstain": "Use when context is insufficient to label confidently.",
    "needs_review": "Flag items that require supervisor follow-up or contain conflicting info.",
    "label_status": "Mark `resolved` once the case has been adjudicated or synced into the gold set.",
}

FIELD_GLOSSARY: Dict[str, str] = {
    "Priority": LABEL_TIPS["priority"],
    "size_feet": "`size_feet` is HSOC's estimate of the encampment or belongings footprint in feet. It reflects linear spread, not square footage.",
    "hours_to_resolution": "Time between the initial request and the last known update/closure.",
    "status_notes": "311 or responder notes at closure. Often describe remediation or why the case was closed.",
    "resolution_notes": "Post-closure follow-up notes when provided by HSOC.",
}


def keyboard_button(
    label: str,
    shortcuts: ShortcutType | None = None,
    *,
    button_type: str = "secondary",
    use_container_width: bool = False,
    key: Optional[str] = None,
    help: Optional[str] = None,
) -> bool:
    """Render a button with optional keyboard shortcuts."""

    if shortcuts and shortcut_button is not None:
        return shortcut_button(
            label,
            shortcuts,
            None,
            key=key,
            help=help,
            type=button_type,
            use_container_width=use_container_width,
            hint=True,
        )
    return st.button(
        label,
        key=key,
        help=help,
        type=button_type,
        use_container_width=use_container_width,
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


def get_supabase_client() -> Optional[Client]:
    if not SUPABASE_URL or not SUPABASE_KEY or create_client is None:
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        st.error("Failed to initialize Supabase client. Falling back to file storage.")
        return None


@st.cache_data
def load_all_labels(base_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    base = Path(base_dir)
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not base.exists():
        return out
    for path in base.rglob("*.jsonl"):
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    rec = json.loads(s)
                    rid = rec.get("request_id")
                    if rid is None:
                        continue
                    rec["features"] = coerce_features(rec)
                    out.setdefault(str(rid), []).append(rec)
        except Exception:
            continue
    return out


def load_labels_supabase(client: Client) -> Dict[str, List[Dict[str, Any]]]:  # pragma: no cover - requires Supabase
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
        out.setdefault(str(rid), []).append(row)
    return out


@st.cache_data
def load_users(path: str) -> Dict[str, Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        st.warning("Users file missing; defaulting to open annotator mode")
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {u["id"]: u for u in data.get("users", []) if "id" in u}


def todays_label_file() -> Path:
    day = datetime.utcnow().strftime("%Y%m%d")
    run_dir = LABELS_DIR / day
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / "labels.jsonl"


def save_label(
    payload: Dict[str, Any], supabase_client: Optional[Client], enable_file_backup: bool
) -> None:
    if supabase_client is not None:  # pragma: no cover - requires Supabase
        try:
            supabase_client.table("labels").insert(payload).execute()
        except Exception as exc:
            st.error(f"Failed to write label to Supabase: {exc}")
            return
    if enable_file_backup or supabase_client is None:
        target = todays_label_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


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


def record_feature_defaults(record: Dict[str, Any]) -> Dict[str, Any]:
    def bool_default(value: Any) -> Optional[bool]:
        if value is None:
            return None
        return bool(value)

    defaults: Dict[str, Any] = {
        "lying_face_down": record.get("tag_lying_face_down") is True,
        "safety_issue": record.get("tag_safety_issue") is True,
        "drugs": record.get("tag_drugs") is True,
        "tents_present": record.get("tag_tents_present") is True,
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

    return {k: v for k, v in defaults.items() if v is not None}


def authenticate(users: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not users:
        username = st.text_input("Annotator ID", value=st.session_state.get("annotator_id", ""))
        st.session_state["annotator_id"] = username.strip()
        if username.strip():
            return {"id": username.strip(), "name": username.strip(), "role": "annotator"}
        st.stop()
    if "user" in st.session_state:
        return st.session_state["user"]
    with st.form("login_form", clear_on_submit=False):
        st.subheader("Sign in")
        username = st.text_input("Annotator ID")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login", type="primary")
        if submitted:
            user = users.get(username)
            if user and verify_password(password, user.get("password_hash", "")):
                st.session_state["user"] = user
                return user
            st.error("Invalid credentials")
    st.stop()
    return None


def authenticate_supabase(client: Client) -> Optional[Dict[str, Any]]:  # pragma: no cover - requires Supabase
    session = st.session_state.get("sb_session")
    if session:
        return session

    tab_login, tab_signup = st.tabs(["Sign in", "Sign up"])

    with tab_login:
        st.subheader("Sign in")
        with st.form("supabase_signin"):
            email = st.text_input("Email", key="signin_email")
            password = st.text_input("Password", type="password", key="signin_password")
            submitted = st.form_submit_button("Sign in", type="primary")
            if submitted:
                try:
                    result = client.auth.sign_in_with_password({"email": email.strip(), "password": password})
                    user = result.user
                    if user is None:
                        st.error("Sign in failed. Check credentials or verify your email.")
                    else:
                        profile = {
                            "id": user.id,
                            "email": user.email,
                            "name": user.user_metadata.get("display_name") or user.email,
                            "role": user.user_metadata.get("role", "annotator"),
                        }
                        st.session_state["sb_session"] = profile
                        st.rerun()
                except Exception as exc:
                    st.error(f"Sign in error: {exc}")

    with tab_signup:
        st.subheader("Create account")
        with st.form("supabase_signup"):
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")
            display_name = st.text_input("Display name (shown in app)", key="signup_display_name")
            submitted = st.form_submit_button("Sign up")
            if submitted:
                try:
                    result = client.auth.sign_up(
                        {
                            "email": email.strip(),
                            "password": password,
                            "options": {"data": {"display_name": display_name.strip(), "role": "annotator"}},
                        }
                    )
                    user = result.user
                    if user is None:
                        st.info("Check your email to complete registration, then sign in.")
                    else:
                        st.success("Account created. Please sign in.")
                        st.rerun()
                except Exception as exc:
                    st.error(f"Sign up error: {exc}")

    st.stop()
    return None


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
        req_status = request_status(labels)
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
            has_context = bool(r.get("has_photo") or r.get("status_notes") or r.get("resolution_notes"))
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
    if supabase_client is not None:
        user = authenticate_supabase(supabase_client)
    else:
        users = load_users(str(USERS_FILE))
        user = authenticate(users)
    if user is None:
        st.stop()

    annotator_uid = str(user.get("id"))
    annotator_role = user.get("role", "annotator")
    annotator_display = (
        user.get("name")
        or user.get("display_name")
        or user.get("email")
        or annotator_uid
    )

    if st.sidebar.button("Log out"):
        if supabase_client is not None:
            try:
                supabase_client.auth.sign_out()
            except Exception:
                pass
            st.session_state.pop("sb_session", None)
        else:
            st.session_state.pop("user", None)
        st.rerun()

    rows_all = load_rows(RAW)
    if supabase_client is not None:
        labels_by_request = load_labels_supabase(supabase_client)
    else:
        labels_by_request = load_all_labels(str(LABELS_DIR))

    status_counts: Dict[str, int] = {}
    for record in rows_all:
        rid = str(record.get("request_id"))
        labels = labels_by_request.get(rid, [])
        status = request_status(labels)
        status_counts[status] = status_counts.get(status, 0) + 1
    with_images = sum(1 for r in rows_all if r.get("has_photo"))
    with_notes = sum(1 for r in rows_all if r.get("status_notes"))
    with_resolution_notes = sum(1 for r in rows_all if r.get("resolution_notes"))

    if BACKUP_SETTING is None:
        enable_file_backup = supabase_client is None
    else:
        enable_file_backup = BACKUP_SETTING not in {"0", "false", "False"}

    st.sidebar.header("Queue filters")
    with st.sidebar.expander("Queue snapshot", expanded=False):
        st.write(f"Total requests: {len(rows_all)}")
        for key in ["unlabeled", "needs_review", "conflict", "labeled"]:
            st.write(f"{key.replace('_', ' ').title()}: {status_counts.get(key, 0)}")
        st.write("—")
        st.write(f"With photos: {with_images}")
        st.write(f"With status notes: {with_notes}")
        st.write(f"With resolution notes: {with_resolution_notes}")

    has_photo = st.sidebar.selectbox("Has photo?", ["any", "with photos", "no photos"])
    has_photo = None if has_photo == "any" else (True if has_photo == "with photos" else False)
    kw_opts = ["passed_out", "blocking", "onramp", "propane", "fire", "children", "wheelchair"]
    kw_filters = st.sidebar.multiselect("Must include keywords", kw_opts, default=[])
    tag_opts = ["lying_face_down", "tents_present"]
    tag_filters = st.sidebar.multiselect("Must include tags", tag_opts, default=[])
    status_filter = st.sidebar.selectbox(
        "Request status",
        ["unlabeled", "needs_review", "conflict", "labeled", "all"],
        index=0,
    )
    search_text = st.sidebar.text_input(
        "Search queue",
        value=st.session_state.get("queue_search", ""),
        placeholder="Request ID or keywords",
    )
    st.session_state["queue_search"] = search_text
    only_mine = st.sidebar.checkbox("Only requests I've labeled", value=False)
    require_rich_context = st.sidebar.checkbox(
        "Require photos or notes",
        value=False,
        help="When enabled, the queue will only include requests that already have photos or 311 responder notes.",
    )
    sort_options = [
        "Rich context first",
        "Random",
        "Oldest first",
        "Newest first",
        "Request ID",
    ]
    sort_mode = st.sidebar.selectbox(
        "Sort order",
        sort_options,
        index=0,
        help="Rich context surfaces requests that already have photos, closure notes, or metadata so annotators can move quickly on well-documented cases.",
    )
    seed = st.sidebar.number_input("Random seed", value=42, step=1)
    if st.sidebar.button("Reset queue"):
        st.session_state["reset"] = True

    st.sidebar.caption(
        "Hotkeys: Save ↦ Ctrl/Cmd+Enter • Prev ↦ Shift/Alt+Left • Skip ↦ Shift/Alt+Right"
    )

    rows = subset(
        rows_all,
        has_photo=has_photo,
        kw_filters=kw_filters,
        tag_filters=tag_filters,
        status_filter=status_filter,
        labels_by_request=labels_by_request,
        annotator_uid=annotator_uid,
        search_text=search_text,
        only_mine=only_mine,
        require_rich_context=require_rich_context,
    )
    if sort_mode == "Rich context first":
        rows.sort(
            key=lambda r: (
                -rich_context_score(r),
                parse_created_at(r.get("created_at")) or datetime.max,
            )
        )
    elif sort_mode == "Random":
        random.Random(seed).shuffle(rows)
    elif sort_mode == "Oldest first":
        rows.sort(key=lambda r: parse_created_at(r.get("created_at")) or datetime.max)
    elif sort_mode == "Newest first":
        rows.sort(key=lambda r: parse_created_at(r.get("created_at")) or datetime.min, reverse=True)
    elif sort_mode == "Request ID":
        rows.sort(key=lambda r: str(r.get("request_id") or ""))

    if "idx" not in st.session_state or st.session_state.get("reset"):
        st.session_state.idx = 0
        st.session_state["reset"] = False

    if not rows:
        st.warning("No items matching the filters. Adjust the sidebar.")
        st.stop()

    idx = st.session_state.idx
    idx = max(0, min(idx, len(rows) - 1))
    record = rows[idx]
    req_id = str(record.get("request_id"))
    existing_labels = sort_labels(labels_by_request.get(req_id, []))
    current_status = request_status(existing_labels)

    queue_col, progress_col, idx_col, elapsed_col = st.columns([2, 2, 1, 1])
    queue_col.metric("Queue size", len(rows))
    progress_col.metric("Unique labeled", len([r for r in labels_by_request if labels_by_request[r]]))
    idx_col.metric("Index", f"{idx + 1}/{len(rows)}")
    elapsed_col.metric(
        "Resolution time",
        format_duration_hours(record.get("hours_to_resolution")),
    )
    st.caption(
        f"Signed in as {annotator_display} ({annotator_role}) — Request status: {current_status}"
    )

    images = resolve_images(record)

    left, right = st.columns([3, 2])
    with left:
        st.subheader(f"Request {req_id or '—'}")
        st.caption(
            f"Created: {record.get('created_at') or '—'} | District: {record.get('police_district') or '—'}"
        )
        st.write(record.get("text") or "(No description)")
        st.markdown("---")
        st.markdown("#### Images")
        if not images:
            st.info("No images for this report.")
        else:
            cols = st.columns(min(3, len(images)))
            for i, info in enumerate(images[:9]):
                source = info["local_path"] or info["url"]
                caption_parts = []
                if info["local_path"]:
                    caption_parts.append("cached")
                if info["status"] and info["status"] != "ok":
                    caption_parts.append(info["status"])
                caption = " | ".join(caption_parts) if caption_parts else None
                with cols[i % len(cols)]:
                    st.image(source, use_container_width=True, caption=caption)
            if len(images) > 9:
                st.info(f"Showing first 9 images ({len(images)} total)")

    with right:
        st.markdown("#### Label entry")
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
        if latest_for_user and keyboard_button(
            "Load my last label",
            shortcuts=["shift+l"],
            button_type="secondary",
            use_container_width=True,
        ):
            st.session_state["prefill"] = latest_for_user
            st.rerun()

        prefill = st.session_state.pop("prefill", latest_for_user)
        prefill_features = coerce_features(prefill) if prefill else {}
        record_defaults = record_feature_defaults(record)
        initial_features = {**record_defaults, **prefill_features}

        def prefill_bool(key: str) -> bool:
            return bool(initial_features.get(key, False))

        def prefill_select(key: str, fallback: str, options: List[str]) -> int:
            value = initial_features.get(key, fallback)
            if value not in options:
                value = fallback
            return options.index(value)

        prio_options = ["P1", "P2", "P3", "P4"]
        if prefill and prefill.get("priority") in prio_options:
            prio_index = prio_options.index(prefill["priority"])
        else:
            prio_index = 2
        prio = st.radio(
            "Priority",
            prio_options,
            horizontal=True,
            index=prio_index,
            help=LABEL_TIPS["priority"],
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            lying = st.checkbox(
                "lying_face_down",
                value=prefill_bool("lying_face_down"),
                help=FEATURE_TIPS["lying_face_down"],
            )
            safety = st.checkbox(
                "safety_issue",
                value=prefill_bool("safety_issue"),
                help=FEATURE_TIPS["safety_issue"],
            )
            drugs = st.checkbox(
                "drugs",
                value=prefill_bool("drugs"),
                help=FEATURE_TIPS["drugs"],
            )
        with c2:
            tents = st.checkbox(
                "tents_present",
                value=prefill_bool("tents_present"),
                help=FEATURE_TIPS["tents_present"],
            )
            blocking = st.checkbox(
                "blocking",
                value=prefill_bool("blocking"),
                help=FEATURE_TIPS["blocking"],
            )
            onramp = st.checkbox(
                "on_ramp",
                value=prefill_bool("on_ramp"),
                help=FEATURE_TIPS["on_ramp"],
            )
        with c3:
            propane = st.checkbox(
                "propane_or_flame",
                value=prefill_bool("propane_or_flame"),
                help=FEATURE_TIPS["propane_or_flame"],
            )
            kids = st.checkbox(
                "children_present",
                value=prefill_bool("children_present"),
                help=FEATURE_TIPS["children_present"],
            )
            chair = st.checkbox(
                "wheelchair",
                value=prefill_bool("wheelchair"),
                help=FEATURE_TIPS["wheelchair"],
            )

        num_people_opts = ["0", "1", "2-3", "4-5", "6+"]
        num_people_bin = st.selectbox(
            "num_people_bin",
            num_people_opts,
            index=prefill_select("num_people_bin", "1", num_people_opts),
            help=FEATURE_TIPS["num_people_bin"],
        )
        size_opts = ["0", "1-20", "21-80", "81-150", "150+"]
        size_feet_bin = st.selectbox(
            "size_feet_bin",
            size_opts,
            index=prefill_select("size_feet_bin", "21-80", size_opts),
            help=FEATURE_TIPS["size_feet_bin"],
        )

        confidence_opts = ["High", "Medium", "Low"]
        confidence_default = (
            prefill.get("confidence") if prefill and prefill.get("confidence") in confidence_opts else "Medium"
        )
        confidence = st.selectbox(
            "Confidence",
            confidence_opts,
            index=confidence_opts.index(confidence_default),
            help=LABEL_TIPS["confidence"],
        )

        info_source_options = [
            "Photos",
            "Description text",
            "Status notes",
            "Resolution notes",
            "Prior labels",
            "Map/location",
            "Other external context",
        ]
        prefill_sources = (
            prefill.get("evidence_sources") if prefill else []
        )
        valid_sources = [s for s in prefill_sources or [] if s in info_source_options]
        info_sources = st.multiselect(
            "Information used",
            info_source_options,
            default=valid_sources or ["Photos"] if record.get("has_photo") else valid_sources,
            help=LABEL_TIPS["evidence_sources"],
        )

        abstain = st.checkbox(
            "Abstain (not sure)",
            value=bool(prefill.get("abstain")) if prefill else False,
            help=LABEL_TIPS["abstain"],
        )
        needs_review = st.checkbox(
            "Flag for review",
            value=bool(prefill.get("needs_review")) if prefill else False,
            help=LABEL_TIPS["needs_review"],
        )
        label_status = st.selectbox(
            "Label status",
            ["pending", "resolved"],
            index=0 if not prefill or prefill.get("status") != "resolved" else 1,
            help=LABEL_TIPS["label_status"],
        )
        notes_val = prefill.get("notes") if prefill else ""
        notes = st.text_area(
            "Notes (optional)",
            value=notes_val or "",
            height=90,
            help=LABEL_TIPS["notes"],
        )

        st.divider()
        st.markdown("#### Context & History")
        keywords = [k.replace('kw_', '') for k in record.keys() if k.startswith('kw_') and record[k]]
        summary_tab, history_tab, raw_tab = st.tabs(["Summary", "Annotation history", "Raw data"])

        with summary_tab:
            status_value = record.get("status") or "—"
            status_lower = str(record.get("status") or "").lower()
            closed_states = {"closed", "completed", "resolved"}
            status_state = "Closed" if status_lower in closed_states else "Open"
            summary_rows = [
                ("Created", format_timestamp(record.get("created_at"))),
                ("Updated", format_timestamp(record.get("updated_at"))),
                ("Status", f"{status_value} ({status_state})" if status_value != "—" else status_value),
                ("Resolution time", format_duration_hours(record.get("hours_to_resolution"))),
                ("District", record.get("police_district") or "—"),
                (
                    "Neighborhood",
                    record.get("neighborhoods_sffind_boundaries") or "—",
                ),
                ("Service subtype", record.get("service_subtype") or "—"),
                ("Status notes", record.get("status_notes") or "—"),
                ("Resolution notes", record.get("resolution_notes") or "—"),
                ("After action", record.get("after_action_url") or "—"),
                ("Location", f"{record.get('lat')}, {record.get('lon')}")
                if record.get("lat") and record.get("lon")
                else ("Location", "—"),
                ("Keywords", ", ".join(keywords) or "—"),
            ]
            summary_df = pd.DataFrame(summary_rows, columns=["Attribute", "Value"])
            st.dataframe(summary_df, use_container_width=True, hide_index=True)

            auto_flags = {
                "lying_face_down": record.get("tag_lying_face_down"),
                "tents_present": record.get("tag_tents_present"),
                "num_people": record.get("tag_num_people"),
                "size_feet": record.get("tag_size_feet"),
                "safety_issue": record.get("tag_safety_issue"),
                "drugs": record.get("tag_drugs"),
            }
            auto_df = pd.DataFrame([auto_flags])
            st.caption("Auto-tags from transform")
            st.dataframe(auto_df, use_container_width=True, hide_index=True)

        with history_tab:
            if existing_labels:
                history_df = pd.DataFrame(
                    [
                        {
                            "timestamp": format_timestamp(entry.get("timestamp")),
                            "annotator": entry.get("annotator_display")
                            or entry.get("annotator")
                            or entry.get("annotator_uid")
                            or "—",
                            "priority": entry.get("priority")
                            or entry.get("labels", {}).get("priority"),
                            "confidence": entry.get("confidence") or "",
                            "evidence": ", ".join(entry.get("evidence_sources") or []),
                            "status": entry.get("status")
                            or ("needs_review" if entry.get("needs_review") else "pending"),
                            "notes": entry.get("notes") or "",
                        }
                        for entry in existing_labels
                    ]
                )
                st.dataframe(history_df, use_container_width=True, hide_index=True)
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
                st.json(existing_labels)

    col_prev, col_save, col_skip = st.columns([1, 1, 1])

    with col_prev:
        if keyboard_button(
            "Prev",
            shortcuts=["shift+left", "alt+left"],
            use_container_width=True,
        ):
            st.session_state.idx = max(idx - 1, 0)
            st.rerun()

    with col_save:
        if keyboard_button(
            "Save & Next",
            shortcuts=["ctrl+enter", "cmd+enter"],
            button_type="primary",
            use_container_width=True,
        ):
            label_id = str(uuid.uuid4())
            payload = {
                "label_id": label_id,
                "request_id": req_id,
                "annotator_uid": annotator_uid,
                "annotator": annotator_display,
                "annotator_display": annotator_display,
                "role": annotator_role,
                "timestamp": datetime.utcnow().isoformat(),
                "priority": prio,
                "features": {
                    "lying_face_down": lying,
                    "safety_issue": safety,
                    "drugs": drugs,
                    "tents_present": tents,
                    "blocking": blocking,
                    "on_ramp": onramp,
                    "propane_or_flame": propane,
                    "children_present": kids,
                    "wheelchair": chair,
                    "num_people_bin": num_people_bin,
                    "size_feet_bin": size_feet_bin,
                },
                "abstain": bool(abstain),
                "needs_review": bool(needs_review),
                "status": label_status,
                "notes": notes.strip() or None,
                "confidence": confidence,
                "evidence_sources": info_sources,
                "image_paths": record.get("image_paths"),
                "image_checksums": record.get("image_checksums"),
                "revision_of": prefill.get("label_id") if prefill else None,
            }
            save_label(payload, supabase_client, enable_file_backup)
            if supabase_client is None and enable_file_backup:
                load_all_labels.clear()
            st.session_state.idx = min(idx + 1, len(rows) - 1)
            st.rerun()

    with col_skip:
        if keyboard_button(
            "Skip",
            shortcuts=["shift+right", "alt+right"],
            use_container_width=True,
        ):
            st.session_state.idx = min(idx + 1, len(rows) - 1)
            st.rerun()


if __name__ == "__main__":
    main()
