from scripts.labeler_app import suggest_outcome


def test_suggest_invalid_from_notes():
    rec = {"status_notes": "Case is invalid. person standing."}
    value, reason = suggest_outcome(rec)
    assert value == "invalid_report"
    assert "Suggested from notes" in reason


def test_suggest_gone_on_arrival():
    rec = {"resolution_notes": "Responder notes: gone on arrival."}
    value, _ = suggest_outcome(rec)
    assert value == "unable_to_locate"


def test_suggest_delivered_keywords():
    rec = {"status": "Service delivered and resolved"}
    value, _ = suggest_outcome(rec)
    assert value == "service_delivered"
