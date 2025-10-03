#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import random
import sys
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
from streamlit_browser_storage import LocalStorage

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
)

APP_TITLE = "SF311 Priority Labeler — Human-in-the-Loop"


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


SESSION_STORAGE = LocalStorage(key="supabase_session")
SESSION_TOKEN_NAME = "session_tokens"
STATE_TOKEN_KEY = "sb_tokens"
STATE_TOKEN_TRIES = "sb_token_load_tries"


def load_tokens_from_storage() -> Optional[Dict[str, str]]:
    tokens = st.session_state.get(STATE_TOKEN_KEY)
    if (
        isinstance(tokens, dict)
        and tokens.get("access_token")
        and tokens.get("refresh_token")
    ):
        st.session_state[STATE_TOKEN_TRIES] = 0
        return tokens
    try:
        stored = SESSION_STORAGE.get(SESSION_TOKEN_NAME)
    except Exception:
        stored = None
    if (
        isinstance(stored, dict)
        and stored.get("access_token")
        and stored.get("refresh_token")
    ):
        st.session_state[STATE_TOKEN_KEY] = stored
        st.session_state[STATE_TOKEN_TRIES] = 0
        return stored
    st.session_state[STATE_TOKEN_TRIES] = st.session_state.get(STATE_TOKEN_TRIES, 0) + 1
    return None


def save_tokens(access_token: str, refresh_token: str) -> None:
    tokens = {"access_token": access_token, "refresh_token": refresh_token}
    st.session_state[STATE_TOKEN_KEY] = tokens
    st.session_state[STATE_TOKEN_TRIES] = 0
    try:
        SESSION_STORAGE.set(SESSION_TOKEN_NAME, tokens)
    except Exception:
        pass


def clear_tokens() -> None:
    st.session_state.pop(STATE_TOKEN_KEY, None)
    st.session_state[STATE_TOKEN_TRIES] = 0
    try:
        SESSION_STORAGE.delete(SESSION_TOKEN_NAME)
    except Exception:
        pass


def restore_supabase_session(
    client: Client,
) -> None:  # pragma: no cover - requires Supabase
    tokens = load_tokens_from_storage()
    if not tokens:
        return
    try:
        session_result = client.auth.set_session(
            tokens["access_token"], tokens["refresh_token"]
        )
        current = (
            session_result.session if session_result else client.auth.get_session()
        )
        if current and current.access_token and current.refresh_token:
            save_tokens(current.access_token, current.refresh_token)
        if "sb_session" not in st.session_state:
            user_resp = client.auth.get_user()
            user = getattr(user_resp, "user", None)
            if user is not None:
                metadata = getattr(user, "user_metadata", {}) or {}
                profile = {
                    "id": user.id,
                    "email": user.email,
                    "name": metadata.get("display_name") or user.email,
                    "role": metadata.get("role", "annotator"),
                }
                st.session_state["sb_session"] = profile
    except Exception:
        clear_tokens()


DATA = Path(get_secret("LABELER_DATA_DIR", "data"))
RAW = DATA / "transformed.jsonl"
LABELS_DIR = Path(get_secret("LABELS_OUTPUT_DIR", str(DATA / "labels")))
MAX_ANNOTATORS = int(get_secret("MAX_ANNOTATORS_PER_REQUEST", "3"))
SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_PUBLISHABLE_KEY = get_secret("SUPABASE_PUBLISHABLE_KEY")
SUPABASE_ANON_KEY = get_secret("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = get_secret("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_KEY = (
    SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY
)
SUPABASE_KEY_KIND = None
if SUPABASE_KEY:
    if SUPABASE_KEY == SUPABASE_SERVICE_ROLE_KEY:
        SUPABASE_KEY_KIND = "service"
    elif SUPABASE_KEY == SUPABASE_PUBLISHABLE_KEY:
        SUPABASE_KEY_KIND = "publishable"
    else:
        SUPABASE_KEY_KIND = "anon"
BACKUP_SETTING = get_secret("LABELS_JSONL_BACKUP")

st.set_page_config(page_title=APP_TITLE, layout="wide")
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 0.8rem !important;
        padding-bottom: 1.6rem !important;
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
    "tents_present": "Tents, makeshift shelters, or similar structures present.",
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
    "tents_present": "Tents or structures present",
    "blocking": "Blocking right-of-way",
    "on_ramp": "Near freeway on/off ramp",
    "propane_or_flame": "Propane, open flame, or generator",
    "children_present": "Children present",
    "wheelchair": "Mobility device mentioned",
    "num_people_bin": "Estimated number of people",
    "size_feet_bin": "Footprint (linear feet)",
}

LABEL_TIPS: Dict[str, str] = {
    "priority": "P1 = life-threatening/immediate, P4 = informational only. Priorities should track urgency and resource need.",
    "confidence": "How certain you are about the assigned priority/features based on available evidence.",
    "evidence_sources": "Select the sources you relied on (photos, notes, prior history, etc.).",
    "notes": "Capture rationale, escalation paths, or anomalies for reviewers.",
    "abstain": "Use when context is insufficient to label confidently.",
    "needs_review": "Flag items that require supervisor follow-up or contain conflicting info.",
    "label_status": "Mark `resolved` once the case has been adjudicated or synced into the gold set.",
    "outcome_alignment": "How the observed outcome aligns with expectations or service goals.",
    "follow_up_need": "Additional services that would help this case (multi-select).",
}

OUTCOME_OPTIONS: List[Tuple[str, str]] = [
    ("", "Select outcome alignment"),
    ("service_delivered", "Service delivered / resolved"),
    ("client_declined", "Client declined or not interested"),
    ("unable_to_locate", "Unable to locate client"),
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
    "needs_review",
    "conflict",
    "labeled",
    "all",
]


def outcome_display(value: Optional[str]) -> str:
    if not value:
        return "—"
    for key, label in OUTCOME_OPTIONS:
        if key == value:
            return label
    return value.replace("_", " ").title()


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


def get_supabase_client() -> Optional[Client]:
    if not SUPABASE_URL or not SUPABASE_KEY or create_client is None:
        if not SUPABASE_URL:
            st.error(
                "SUPABASE_URL is not configured. Add it to Streamlit secrets or environment variables."
            )
        elif not SUPABASE_KEY:
            st.error(
                "Supabase key is missing. Provide SUPABASE_PUBLISHABLE_KEY or SUPABASE_ANON_KEY in secrets/environment."
            )
        elif create_client is None:
            st.error(
                "supabase-py is not installed. Run 'uv add supabase' or sync dependencies via 'make init'."
            )
        return None
    if SUPABASE_KEY_KIND == "service" and not st.session_state.get(
        "_service_key_warned", False
    ):
        st.warning(
            "Streamlit is using a Supabase service-role key. Switch to a publishable/anon key for client-side sessions.",
        )
        st.session_state["_service_key_warned"] = True
    try:
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        restore_supabase_session(client)
        return client
    except Exception:
        st.error("Failed to initialize Supabase client. Falling back to file storage.")
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


def status_badge(record: Dict[str, Any]) -> str:
    status_raw = str(record.get("status") or "Unknown").strip()
    closed_states = {"closed", "completed", "resolved"}
    if status_raw.lower() in closed_states:
        return f"🔴 {status_raw.title()}"
    if status_raw.lower() in {"open", "assigned", "in progress"}:
        return f"🟢 {status_raw.title()}"
    return f"🟡 {status_raw.title()}"


def outcome_highlight(record: Dict[str, Any]) -> str:
    status = str(record.get("status") or "Status unknown").strip()
    duration = record.get("hours_to_resolution")
    parts: List[str] = []
    if status:
        parts.append(status.title())
    if duration is not None:
        parts.append(f"after {format_duration_hours(duration)}")
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


def authenticate_supabase(
    client: Client,
) -> Optional[Dict[str, Any]]:  # pragma: no cover - requires Supabase
    session = st.session_state.get("sb_session")
    if session:
        return session

    if st.session_state.get(STATE_TOKEN_TRIES, 0) == 1:
        st.info("Restoring session…")
        st.stop()

    notice = st.session_state.pop("sb_notice", None)
    if notice:
        level, message = notice
        if level == "success":
            st.success(message)
        elif level == "error":
            st.error(message)
        else:
            st.info(message)

    tab_login, tab_signup = st.tabs(["Sign in", "Sign up"])

    with tab_login:
        st.subheader("Sign in")
        with st.form("supabase_signin"):
            email = st.text_input("Email", key="signin_email")
            password = st.text_input("Password", type="password", key="signin_password")
            submitted = st.form_submit_button("Sign in", type="primary")
            if submitted:
                try:
                    result = client.auth.sign_in_with_password(
                        {"email": email.strip(), "password": password}
                    )
                    user = result.user
                    if user is None:
                        st.error(
                            "Sign in failed. Check credentials or verify your email."
                        )
                    else:
                        if (
                            result.session
                            and result.session.access_token
                            and result.session.refresh_token
                        ):
                            save_tokens(
                                result.session.access_token,
                                result.session.refresh_token,
                            )
                        profile = {
                            "id": user.id,
                            "email": user.email,
                            "name": user.user_metadata.get("display_name")
                            or user.email,
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
            display_name = st.text_input(
                "Display name (shown in app)", key="signup_display_name"
            )
            submitted = st.form_submit_button("Sign up")
            if submitted:
                try:
                    result = client.auth.sign_up(
                        {
                            "email": email.strip(),
                            "password": password,
                            "options": {
                                "data": {
                                    "display_name": display_name.strip(),
                                    "role": "annotator",
                                }
                            },
                        }
                    )
                    user = result.user
                    session_resp = getattr(result, "session", None)
                    email_confirmed = False
                    if session_resp and getattr(session_resp, "access_token", None):
                        email_confirmed = True
                    if user is not None and getattr(user, "email_confirmed_at", None):
                        email_confirmed = True

                    if not email_confirmed:
                        st.session_state["sb_notice"] = (
                            "info",
                            "Verification email sent. Confirm your address, then return to sign in.",
                        )
                        st.rerun()
                    else:
                        if (
                            result.session
                            and result.session.access_token
                            and result.session.refresh_token
                        ):
                            save_tokens(
                                result.session.access_token,
                                result.session.refresh_token,
                            )
                        st.session_state["sb_notice"] = (
                            "success",
                            "Account created. You can sign in now.",
                        )
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

    user = authenticate_supabase(supabase_client)
    if user is None:
        if (
            st.session_state.get(STATE_TOKEN_TRIES, 0) > 0
            and st.session_state.get(STATE_TOKEN_TRIES, 0) <= 1
        ):
            st.info("Restoring session…")
            st.stop()
        st.stop()

    annotator_uid = str(user.get("id"))
    annotator_role = user.get("role", "annotator")
    annotator_display = (
        user.get("name")
        or user.get("display_name")
        or user.get("email")
        or annotator_uid
    )

    NOTE_STATE_KEY = "note_text"
    NOTE_REQ_KEY = "note_text_request_id"

    def reset_note_state() -> None:
        st.session_state.pop(NOTE_STATE_KEY, None)
        st.session_state.pop(NOTE_REQ_KEY, None)

    undo_context = st.session_state.get("undo_context")
    if undo_context:
        undo_container = st.container()
        with undo_container:
            st.success(f"Saved request {undo_context['request_id']}.", icon="✅")
            if st.button(
                "Undo last save",
                key="undo_last_save",
                help="Removes the most recent label you saved and returns you to that request.",
            ):
                label_id_to_delete = undo_context.get("label_id")
                if label_id_to_delete and delete_label(
                    label_id_to_delete, supabase_client
                ):
                    st.session_state.idx = undo_context.get("previous_idx", 0)
                    previous_prefill = undo_context.get("previous_prefill")
                    if previous_prefill:
                        st.session_state["prefill"] = previous_prefill
                    request_ref = undo_context.get("request_id", "")
                    st.session_state["queue_search"] = str(request_ref)
                    st.session_state.pop("undo_context", None)
                    st.rerun()
            if st.button(
                "Dismiss",
                key="dismiss_save_notice",
                help="Hide this message without undoing the label.",
            ):
                st.session_state.pop("undo_context", None)
                st.rerun()

    if st.sidebar.button("Log out"):
        try:
            supabase_client.auth.sign_out()
        except Exception:
            pass
        st.session_state.pop("sb_session", None)
        clear_tokens()
        st.rerun()

    rows_all = load_rows(RAW)
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
                    "status": entry.get("status")
                    or ("needs_review" if entry.get("needs_review") else "pending"),
                    "outcome_alignment": entry.get("outcome_alignment"),
                    "follow_up_need": entry.get("follow_up_need"),
                    "raw": entry,
                }
            )

    my_labels.sort(
        key=lambda item: parse_iso(item.get("timestamp")) or datetime.min, reverse=True
    )

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
        enable_file_backup = False
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
                pd.DataFrame(preview_rows), use_container_width=True, hide_index=True
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
                st.session_state["jump_target"] = str(entry["request_id"])
                st.session_state["status_filter"] = "all"
                st.session_state.pop("undo_context", None)
                st.session_state["reset"] = True
                st.rerun()
        else:
            st.caption("No saved labels yet.")

    has_photo = st.sidebar.selectbox("Has photo?", ["any", "with photos", "no photos"])
    has_photo = (
        None if has_photo == "any" else (True if has_photo == "with photos" else False)
    )
    kw_labels = [label for _, label in KEYWORD_FILTER_OPTIONS]
    kw_label_to_key = {label: key for key, label in KEYWORD_FILTER_OPTIONS}
    kw_selected_labels = st.sidebar.multiselect(
        "Must include keywords", kw_labels, default=[]
    )
    kw_filters = [kw_label_to_key[label] for label in kw_selected_labels]

    tag_labels = [label for _, label in TAG_FILTER_OPTIONS]
    tag_label_to_key = {label: key for key, label in TAG_FILTER_OPTIONS}
    tag_selected_labels = st.sidebar.multiselect(
        "Must include tags", tag_labels, default=[]
    )
    tag_filters = [tag_label_to_key[label] for label in tag_selected_labels]
    status_default = st.session_state.get("status_filter", STATUS_FILTER_OPTIONS[0])
    if status_default not in STATUS_FILTER_OPTIONS:
        status_default = STATUS_FILTER_OPTIONS[0]
    status_filter = st.sidebar.selectbox(
        "Request status",
        STATUS_FILTER_OPTIONS,
        index=STATUS_FILTER_OPTIONS.index(status_default),
    )
    st.session_state["status_filter"] = status_filter
    search_text = st.sidebar.text_input(
        "Search queue",
        value=st.session_state.get("queue_search", ""),
        placeholder="Request ID or keywords",
    )
    st.session_state["queue_search"] = search_text
    only_mine_default = bool(st.session_state.get("only_mine", False))
    only_mine = st.sidebar.checkbox(
        "Only requests I've labeled", value=only_mine_default
    )
    st.session_state["only_mine"] = only_mine
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
        rows.sort(
            key=lambda r: parse_created_at(r.get("created_at")) or datetime.min,
            reverse=True,
        )
    elif sort_mode == "Request ID":
        rows.sort(key=lambda r: str(r.get("request_id") or ""))

    jump_target = st.session_state.pop("jump_target", None)
    jumped = False
    if jump_target:
        jump_target = str(jump_target)
        for idx_candidate, candidate in enumerate(rows):
            if str(candidate.get("request_id")) == jump_target:
                st.session_state.idx = idx_candidate
                jumped = True
                break

    if "idx" not in st.session_state or (st.session_state.get("reset") and not jumped):
        st.session_state.idx = 0
        st.session_state["reset"] = False
    elif st.session_state.get("reset"):
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

    prev_clicked = False
    save_clicked = False
    skip_clicked = False

    summary_col, action_col = st.columns([3, 1.6])
    with summary_col:
        st.caption(
            f"Queue {len(rows)} · Labeled {len([r for r in labels_by_request if labels_by_request[r]])} · "
            f"Index {idx + 1}/{len(rows)} · Time to resolution {format_duration_hours(record.get('hours_to_resolution'))}"
        )
        st.markdown(
            f"**Case snapshot:** {status_badge(record)} · {outcome_highlight(record)}"
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

    left, right = st.columns([3, 2])
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
        st.markdown("#### Decision")
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
            width="stretch",
        ):
            st.session_state["prefill"] = latest_for_user
            reset_note_state()
            st.rerun()

        prefill = st.session_state.pop("prefill", latest_for_user)
        prefill_features = coerce_features(prefill) if prefill else {}
        record_defaults = record_feature_defaults(record)
        initial_features = {**record_defaults, **prefill_features}

        def widget_key(name: str) -> str:
            return f"{req_id}_{name}"

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
            key=widget_key("priority"),
        )

        notes_val = (prefill.get("notes") if prefill else "") or ""
        if st.session_state.get(NOTE_REQ_KEY) != req_id:
            st.session_state[NOTE_REQ_KEY] = req_id
            st.session_state[NOTE_STATE_KEY] = str(notes_val)
        notes = st.text_area(
            "Notes",
            key=NOTE_STATE_KEY,
            height=110,
            help=LABEL_TIPS["notes"],
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            lying = st.checkbox(
                FEATURE_DISPLAY_NAMES["lying_face_down"],
                value=prefill_bool("lying_face_down"),
                help=FEATURE_TIPS["lying_face_down"],
                key=widget_key("feature_lying_face_down"),
            )
            safety = st.checkbox(
                FEATURE_DISPLAY_NAMES["safety_issue"],
                value=prefill_bool("safety_issue"),
                help=FEATURE_TIPS["safety_issue"],
                key=widget_key("feature_safety_issue"),
            )
            drugs = st.checkbox(
                FEATURE_DISPLAY_NAMES["drugs"],
                value=prefill_bool("drugs"),
                help=FEATURE_TIPS["drugs"],
                key=widget_key("feature_drugs"),
            )
        with c2:
            tents = st.checkbox(
                FEATURE_DISPLAY_NAMES["tents_present"],
                value=prefill_bool("tents_present"),
                help=FEATURE_TIPS["tents_present"],
                key=widget_key("feature_tents_present"),
            )
            blocking = st.checkbox(
                FEATURE_DISPLAY_NAMES["blocking"],
                value=prefill_bool("blocking"),
                help=FEATURE_TIPS["blocking"],
                key=widget_key("feature_blocking"),
            )
            onramp = st.checkbox(
                FEATURE_DISPLAY_NAMES["on_ramp"],
                value=prefill_bool("on_ramp"),
                help=FEATURE_TIPS["on_ramp"],
                key=widget_key("feature_on_ramp"),
            )
        with c3:
            propane = st.checkbox(
                FEATURE_DISPLAY_NAMES["propane_or_flame"],
                value=prefill_bool("propane_or_flame"),
                help=FEATURE_TIPS["propane_or_flame"],
                key=widget_key("feature_propane_or_flame"),
            )
            kids = st.checkbox(
                FEATURE_DISPLAY_NAMES["children_present"],
                value=prefill_bool("children_present"),
                help=FEATURE_TIPS["children_present"],
                key=widget_key("feature_children_present"),
            )
            chair = st.checkbox(
                FEATURE_DISPLAY_NAMES["wheelchair"],
                value=prefill_bool("wheelchair"),
                help=FEATURE_TIPS["wheelchair"],
                key=widget_key("feature_wheelchair"),
            )

        num_people_opts = ["0", "1", "2-3", "4-5", "6+"]
        num_people_bin = st.selectbox(
            FEATURE_DISPLAY_NAMES["num_people_bin"],
            num_people_opts,
            index=prefill_select("num_people_bin", "1", num_people_opts),
            help=FEATURE_TIPS["num_people_bin"],
            key=widget_key("num_people_bin"),
        )
        size_opts = ["0", "1-20", "21-80", "81-150", "150+"]
        size_feet_bin = st.selectbox(
            FEATURE_DISPLAY_NAMES["size_feet_bin"],
            size_opts,
            index=prefill_select("size_feet_bin", "21-80", size_opts),
            help=FEATURE_TIPS["size_feet_bin"],
            key=widget_key("size_feet_bin"),
        )

        confidence_opts = ["High", "Medium", "Low"]
        confidence_default = (
            prefill.get("confidence")
            if prefill and prefill.get("confidence") in confidence_opts
            else "Medium"
        )
        confidence = st.selectbox(
            "Confidence",
            confidence_opts,
            index=confidence_opts.index(confidence_default),
            help=LABEL_TIPS["confidence"],
            key=widget_key("confidence"),
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
        prefill_outcome = ""
        if prefill:
            raw_outcome = prefill.get("outcome_alignment")
            if raw_outcome in outcome_values:
                prefill_outcome = str(raw_outcome)
        default_outcome_index = outcome_values.index(prefill_outcome)
        outcome_label = st.selectbox(
            "Outcome alignment",
            outcome_labels,
            index=default_outcome_index,
            help=LABEL_TIPS["outcome_alignment"],
            key=widget_key("outcome_alignment"),
        )
        outcome_alignment = outcome_values[outcome_labels.index(outcome_label)]
        if outcome_alignment == "":
            outcome_alignment = None

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

        abstain = st.checkbox(
            "Abstain (not sure)",
            value=bool(prefill.get("abstain")) if prefill else False,
            help=LABEL_TIPS["abstain"],
            key=widget_key("abstain"),
        )
        needs_review = st.checkbox(
            "Flag for review",
            value=bool(prefill.get("needs_review")) if prefill else False,
            help=LABEL_TIPS["needs_review"],
            key=widget_key("needs_review"),
        )
        label_status = st.selectbox(
            "Label status",
            ["pending", "resolved"],
            index=0 if not prefill or prefill.get("status") != "resolved" else 1,
            help=LABEL_TIPS["label_status"],
            key=widget_key("label_status"),
        )

        st.divider()
        st.markdown("#### Context & History")
        keywords = [
            k.replace("kw_", "")
            for k in record.keys()
            if k.startswith("kw_") and record[k]
        ]
        summary_tab, history_tab, raw_tab = st.tabs(
            ["Summary", "Annotation history", "Raw data"]
        )

        with summary_tab:
            latest_any = existing_labels[-1] if existing_labels else None
            summary_rows = [
                ("Created", format_timestamp(record.get("created_at"))),
                ("Updated", format_timestamp(record.get("updated_at"))),
                ("District", record.get("police_district") or "—"),
                (
                    "Neighborhood",
                    record.get("neighborhoods_sffind_boundaries") or "—",
                ),
                ("Service subtype", record.get("service_subtype") or "—"),
                (
                    ("Location", f"{record.get('lat')}, {record.get('lon')}")
                    if record.get("lat") and record.get("lon")
                    else ("Location", "—")
                ),
                ("Keywords", ", ".join(keywords) or "—"),
            ]
            if latest_any:
                summary_rows.insert(
                    1,
                    (
                        "Outcome alignment",
                        outcome_display(latest_any.get("outcome_alignment")),
                    ),
                )
                summary_rows.insert(
                    2,
                    (
                        "Follow-up needs",
                        follow_up_display(latest_any.get("follow_up_need")),
                    ),
                )
            summary_df = pd.DataFrame(summary_rows, columns=["Attribute", "Value"])
            st.dataframe(summary_df, use_container_width=True, hide_index=True)

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
                            or (
                                "needs_review"
                                if entry.get("needs_review")
                                else "pending"
                            ),
                            "outcome": outcome_display(entry.get("outcome_alignment")),
                            "follow_up": follow_up_display(entry.get("follow_up_need")),
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
            width="stretch",
        ):
            prev_clicked = True

    with col_save:
        if keyboard_button(
            "Save & Next",
            shortcuts=["ctrl+enter", "cmd+enter"],
            button_type="primary",
            width="stretch",
        ):
            save_clicked = True

    with col_skip:
        if keyboard_button(
            "Skip",
            shortcuts=["shift+right", "alt+right"],
            width="stretch",
        ):
            skip_clicked = True

    if prev_clicked:
        st.session_state.idx = max(idx - 1, 0)
        reset_note_state()
        st.rerun()
    elif save_clicked:
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
            "outcome_alignment": outcome_alignment,
            "follow_up_need": follow_up_need,
            "image_paths": record.get("image_paths"),
            "image_checksums": record.get("image_checksums"),
            "revision_of": prefill.get("label_id") if prefill else None,
        }
        if save_label(payload, supabase_client, enable_file_backup):
            st.session_state["undo_context"] = {
                "label_id": label_id,
                "request_id": req_id,
                "previous_idx": idx,
                "previous_prefill": deepcopy(prefill) if prefill else None,
                "timestamp": datetime.utcnow().isoformat(),
            }
        st.session_state.idx = min(idx + 1, len(rows) - 1)
        reset_note_state()
        st.rerun()
    elif skip_clicked:
        st.session_state.idx = min(idx + 1, len(rows) - 1)
        reset_note_state()
        st.rerun()


if __name__ == "__main__":
    main()
