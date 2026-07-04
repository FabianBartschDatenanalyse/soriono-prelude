from __future__ import annotations

from pathlib import Path

import pytest

from soriono_prelude.sources import SourceRegistry, reader_for


def _profile(fmt: str, source_url: str) -> dict[str, object]:
    return {
        "resource_id": f"resource-{fmt}",
        "title": f"Resource {fmt}",
        "columns": ["a", "b"],
        "readiness": {},
        "source": {
            "access_method": "direct_download",
            "format": fmt,
            "source_url": source_url,
        },
    }


def test_legacy_xls_is_rejected_with_actionable_message() -> None:
    with pytest.raises(ValueError, match="Legacy XLS"):
        reader_for(_profile("xls", "https://example.test/data.xls"), "https://example.test/data.xls")


def test_xlsx_is_still_readable() -> None:
    reader = reader_for(
        _profile("xlsx", "https://example.test/data.xlsx"),
        "https://example.test/data.xlsx",
    )

    assert reader == "read_xlsx('https://example.test/data.xlsx')"


def test_csv_reader_records_rejected_lines() -> None:
    reader = reader_for(
        _profile("csv", "https://example.test/data.csv"),
        "https://example.test/data.csv",
    )

    assert reader == "read_csv_auto('https://example.test/data.csv', store_rejects=true)"


def test_unsupported_format_does_not_break_registration(tmp_path: Path) -> None:
    registry = SourceRegistry(path=tmp_path / "sources.json")

    record = registry.register_profile(_profile("xls", "https://example.test/data.xls"))

    assert record is None
    assert registry.records == {}


def test_unchanged_profile_registration_does_not_rewrite_registry(tmp_path: Path) -> None:
    registry = SourceRegistry(path=tmp_path / "sources.json")
    profile = _profile("csv", "https://example.test/data.csv")

    first = registry.register_profile(profile)
    assert first is not None
    saved_at = registry.path.stat().st_mtime_ns

    second = registry.register_profile(profile)

    assert second == first
    assert registry.path.stat().st_mtime_ns == saved_at
