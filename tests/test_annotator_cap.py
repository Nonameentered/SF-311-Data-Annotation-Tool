from scripts.labeler_utils import unique_annotators, can_annotator_label
from scripts.labeler_app import passes_minimal_filters


def test_unique_and_can_label_basic():
    labels = [{"annotator_uid": "A"}, {"annotator_uid": "B"}]
    assert unique_annotators(labels) == ["A", "B"]
    assert can_annotator_label(labels, "A", max_annotators=2) is True
    assert can_annotator_label(labels, "C", max_annotators=2) is False


def test_passes_minimal_filters_skips_third_non_mine():
    rec = {"request_id": "1", "has_photo": True}
    labels = [{"annotator_uid": "A"}, {"annotator_uid": "B"}]
    assert (
        passes_minimal_filters(
            rec,
            labels,
            has_photo=None,
            status_filter="all",
            annotator_uid="C",
            max_annotators=2,
        )
        is False
    )


def test_passes_minimal_filters_allows_if_mine_even_at_cap():
    rec = {"request_id": "1", "has_photo": False}
    labels = [{"annotator_uid": "A"}, {"annotator_uid": "B"}]
    assert (
        passes_minimal_filters(
            rec,
            labels,
            has_photo=None,
            status_filter="all",
            annotator_uid="A",
            max_annotators=2,
        )
        is True
    )
