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
        "priority": "High",
        "timestamp": "2024-01-01T10:00:00",
        "review_status": "pending",
    },
    {
        "annotator_uid": "22222222-2222-2222-2222-222222222222",
        "annotator": "bob",
        "priority": "High",
        "timestamp": "2024-01-01T11:00:00",
        "review_status": "agree",
    },
    {
        "annotator_uid": "33333333-3333-3333-3333-333333333333",
        "annotator": "carol",
        "priority": "High",
        "timestamp": "2024-01-01T12:00:00",
        "review_status": "agree",
    },
]


def test_unique_annotators_ordered():
    assert unique_annotators(LABELS) == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
    ]


def test_request_status_requires_review_until_second_annotator():
    single = LABELS[:1]
    assert request_status(single) == "needs_review"


def test_request_status_marks_disagreement_as_needs_review():
    labels = [
        {
            "annotator_uid": "1",
            "annotator": "a",
            "priority": "High",
            "timestamp": "2024-01-01T00:00:00",
            "review_status": "pending",
        },
        {
            "annotator_uid": "2",
            "annotator": "b",
            "priority": "Low",
            "timestamp": "2024-01-01T01:00:00",
            "review_status": "agree",
        },
    ]
    assert request_status(labels) == "needs_review"


def test_request_status_disagree_requires_followup():
    labels = [
        {
            "annotator_uid": "1",
            "annotator": "a",
            "priority": "High",
            "timestamp": "2024-01-01T00:00:00",
            "review_status": "pending",
        },
        {
            "annotator_uid": "2",
            "annotator": "b",
            "priority": "High",
            "timestamp": "2024-01-01T01:00:00",
            "review_status": "disagree",
        },
    ]
    assert request_status(labels) == "needs_review"


def test_request_status_complete_after_review():
    labels = LABELS[:2]
    assert request_status(labels) == "labeled"


def test_can_label_limit_three():
    assert can_annotator_label(LABELS, "22222222-2222-2222-2222-222222222222") is True
    assert can_annotator_label(LABELS, "44444444-4444-4444-4444-444444444444") is False


def test_latest_label_for_annotator():
    latest = latest_label_for_annotator(LABELS, "22222222-2222-2222-2222-222222222222")
    assert latest is not None
    assert latest["priority"] == "High"


def test_password_hash_and_verify():
    pw_hash = hash_password("secret")
    assert verify_password("secret", pw_hash) is True
    assert verify_password("other", pw_hash) is False
