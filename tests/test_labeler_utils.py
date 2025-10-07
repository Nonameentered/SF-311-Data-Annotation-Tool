from __future__ import annotations
from scripts.labeler_utils import (
    can_annotator_label,
    hash_password,
    latest_label_for_annotator,
    request_status,
    unique_annotators,
    verify_password,
)

LABELS = [
    {
        "annotator_uid": "11111111-1111-1111-1111-111111111111",
        "annotator": "alice",
        "priority": "high",
        "timestamp": "2024-01-01T10:00:00",
    },
    {
        "annotator_uid": "22222222-2222-2222-2222-222222222222",
        "annotator": "bob",
        "priority": "medium",
        "timestamp": "2024-01-01T11:00:00",
        "needs_review": True,
    },
    {
        "annotator_uid": "33333333-3333-3333-3333-333333333333",
        "annotator": "carol",
        "priority": "medium",
        "timestamp": "2024-01-01T12:00:00",
    },
]


def test_unique_annotators_ordered():
    assert unique_annotators(LABELS) == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
    ]


def test_request_status_with_needs_review():
    assert request_status(LABELS) == "needs_review"


def test_can_label_limit_three():
    assert can_annotator_label(LABELS, "22222222-2222-2222-2222-222222222222") is True
    assert can_annotator_label(LABELS, "44444444-4444-4444-4444-444444444444") is False


def test_latest_label_for_annotator():
    latest = latest_label_for_annotator(LABELS, "22222222-2222-2222-2222-222222222222")
    assert latest is not None
    assert latest["priority"] == "medium"


def test_password_hash_and_verify():
    pw_hash = hash_password("secret")
    assert verify_password("secret", pw_hash) is True
    assert verify_password("other", pw_hash) is False
