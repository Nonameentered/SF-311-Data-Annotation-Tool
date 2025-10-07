from __future__ import annotations

from datetime import datetime

from scripts.labeler_app import subset


def _base_rows():
    return [
        {
            "request_id": 1,
            "text": "Urgent welfare check needed",
            "has_photo": True,
            "created_at": "2024-01-01T08:00:00",
        },
        {
            "request_id": 2,
            "text": "General follow-up",
            "has_photo": False,
            "created_at": "2024-01-02T09:00:00",
        },
    ]


def test_subset_filters_by_search_text():
    rows = _base_rows()
    labels_by_request: dict[str, list[dict]] = {}

    filtered = subset(
        rows,
        has_photo=None,
        kw_filters=[],
        tag_filters=[],
        status_filter="all",
        labels_by_request=labels_by_request,
        annotator_uid="tester",
        search_text="urgent",
    )

    assert len(filtered) == 1
    assert filtered[0]["request_id"] == 1


def test_subset_only_mine_returns_assigned_rows():
    rows = _base_rows()
    labels_by_request = {
        "1": [
            {
                "annotator_uid": "tester",
                "timestamp": datetime.utcnow().isoformat(),
                "priority": "medium",
            }
        ],
        "2": [
            {
                "annotator_uid": "someone_else",
                "timestamp": datetime.utcnow().isoformat(),
                "priority": "low",
            }
        ],
    }

    filtered = subset(
        rows,
        has_photo=None,
        kw_filters=[],
        tag_filters=[],
        status_filter="all",
        labels_by_request=labels_by_request,
        annotator_uid="tester",
        only_mine=True,
    )

    assert len(filtered) == 1
    assert filtered[0]["request_id"] == 1
