from __future__ import annotations
import importlib
import sys
import types


def ensure_stub(name: str) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    if name == "pandas":
        class DummyFrame(list):
            def to_parquet(self, *_, **__):
                return None

            def to_csv(self, *_, **__):
                return None

        module.DataFrame = lambda rows: DummyFrame(rows)
    elif name == "rich":
        module.print = lambda *_, **__: None
    sys.modules[name] = module


ensure_stub("pandas")
ensure_stub("rich")

sf311_transform = importlib.import_module("scripts.sf311_transform")


def test_normalize_record_includes_manifest_fields():
    rec = {
        "service_request_id": "123",
        "description": "Tent blocking sidewalk",
        "requested_datetime": "2024-01-01T12:00:00",
        "homeless_tags": {"tents_or_makeshift_present": True},
        "photos": ["http://example.com/image1.jpg"],
    }
    manifest = {
        ("123", "http://example.com/image1.jpg"): {
            "local_path": "data/images/123/00_image1.jpg",
            "sha256": "abc123",
            "status": "ok",
        }
    }

    row = sf311_transform.normalize_record(rec, 400.0, manifest)

    assert row["image_paths"] == ["data/images/123/00_image1.jpg"]
    assert row["image_checksums"] == ["abc123"]
    assert row["image_fetch_status"] == ["ok"]
    assert row["has_photo"] is True


def test_normalize_record_handles_missing_manifest():
    rec = {
        "service_request_id": "456",
        "description": "No image url",
        "photos": ["http://example.com/missing.jpg"],
    }
    manifest: dict = {}

    row = sf311_transform.normalize_record(rec, 400.0, manifest)

    assert row["image_paths"] == [None]
    assert row["image_checksums"] == [None]
    assert row["image_fetch_status"] == [None]
