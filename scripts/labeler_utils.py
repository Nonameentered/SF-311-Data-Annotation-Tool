#!/usr/bin/env python3
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional

ISO_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)


def parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    parsed: Optional[datetime] = None
    for fmt in ISO_FORMATS:
        try:
            parsed = datetime.strptime(ts, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(ts)
        except Exception:  # noqa: BLE001
            parsed = None
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def sort_labels(labels: List[Dict]) -> List[Dict]:
    return sorted(labels, key=lambda l: parse_iso(l.get("timestamp")) or datetime.min)


def _label_uid(label: Dict) -> str:
    val = (
        label.get("annotator_uid")
        or label.get("annotator_id")
        or label.get("annotator")
        or label.get("annotator_email")
    )
    return str(val) if val is not None else ""


def unique_annotators(labels: List[Dict]) -> List[str]:
    seen = []
    for lab in sort_labels(labels):
        annot = _label_uid(lab)
        if annot and annot not in seen:
            seen.append(annot)
    return seen


def request_status(labels: List[Dict], required_unique: int = 2) -> str:
    if not labels:
        return "unlabeled"
    ordered = sort_labels(labels)
    annotators = unique_annotators(ordered)
    review_statuses = [
        (str(l.get("review_status")) if l.get("review_status") is not None else "")
        .strip()
        .lower()
        for l in ordered
    ]

    if any(status == "disagree" for status in review_statuses):
        return "needs_review"

    priorities = {
        str(l.get("priority")).strip().lower()
        for l in ordered
        if l.get("priority") is not None
        and str(l.get("priority")).strip() != ""
    }
    if len(priorities) > 1:
        return "needs_review"

    reviewers_present = any(status == "agree" for status in review_statuses)
    if len(annotators) < required_unique or not reviewers_present:
        return "needs_review"

    return "labeled"


def latest_label_for_annotator(labels: List[Dict], annotator: str) -> Optional[Dict]:
    annotator = str(annotator)
    matching = [l for l in labels if _label_uid(l) == annotator]
    if not matching:
        return None
    return sort_labels(matching)[-1]


def latest_label_excluding(labels: List[Dict], annotator: str) -> Optional[Dict]:
    annotator = str(annotator)
    matching = [l for l in labels if _label_uid(l) != annotator]
    if not matching:
        return None
    return sort_labels(matching)[-1]


def can_annotator_label(
    labels: List[Dict], annotator: str, max_annotators: int = 3
) -> bool:
    annotator = str(annotator)
    annotators = unique_annotators(labels)
    if annotator in annotators:
        return True
    return len(annotators) < max_annotators


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return True
    return hash_password(password) == stored_hash
