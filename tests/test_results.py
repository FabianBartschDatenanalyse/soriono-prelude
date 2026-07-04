from __future__ import annotations

from pathlib import Path

import pytest

from soriono_prelude.results import ResultStore
from soriono_prelude.sources import SourceRecord


def source(tmp_path: Path) -> SourceRecord:
    path = tmp_path / "values.csv"
    path.write_text(
        "id,value\n" + "".join(f"{index},value-{index}\n" for index in range(10)),
        encoding="utf-8",
    )
    url = path.resolve().as_posix()
    return SourceRecord(
        source_handle="source:test",
        resource_id="test",
        title="Test",
        source_url=url,
        duckdb_reader=f"read_csv_auto('{url}')",
        format="csv",
        access_method="test",
    )


def test_complete_result_is_stored_and_paged(tmp_path: Path) -> None:
    record = source(tmp_path)
    store = ResultStore(
        tmp_path / "results",
        inline_rows=3,
        max_page_rows=2,
    )

    result = store.execute(
        f"SELECT * FROM {record.sql_name} ORDER BY id",
        [record],
    )

    assert result["status"] == "succeeded"
    assert result["row_count"] == 10
    assert result["returned_count"] == 3
    assert result["truncated"] is True
    assert [row["id"] for row in result["rows"]] == [0, 1, 2]

    page = store.page(result["result_handle"], offset=3, limit=100)
    assert page["limit"] == 2
    assert [row["id"] for row in page["rows"]] == [3, 4]
    assert page["has_more"] is True
    assert page["next_offset"] == 5

    summary = store.summary(result["result_handle"])
    assert summary["row_count"] == 10
    assert summary["resource_ids"] == ["test"]
    assert summary["size_bytes"] > 0


def test_explicit_query_limit_is_preserved_in_stored_result(tmp_path: Path) -> None:
    record = source(tmp_path)
    store = ResultStore(tmp_path / "results", inline_rows=20)

    result = store.execute(
        f"SELECT * FROM {record.sql_name} ORDER BY id",
        [record],
        limit=4,
    )

    assert result["row_count"] == 4
    assert result["returned_count"] == 4
    assert result["truncated"] is False


def test_result_storage_limit_removes_oversized_file(tmp_path: Path) -> None:
    record = source(tmp_path)
    store = ResultStore(tmp_path / "results", max_result_bytes=1)

    result = store.execute(f"SELECT * FROM {record.sql_name}", [record])

    assert result["status"] == "result_too_large"
    assert list((tmp_path / "results").glob("*.parquet")) == []


def test_rejected_csv_lines_produce_a_warning(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_text(
        "id,value\n1,ok\ntoo,many,columns,here\n2,ok\n",
        encoding="utf-8",
    )
    url = path.resolve().as_posix()
    record = SourceRecord(
        source_handle="source:broken",
        resource_id="broken",
        title="Broken",
        source_url=url,
        duckdb_reader=f"read_csv_auto('{url}', store_rejects=true)",
        format="csv",
        access_method="test",
    )
    store = ResultStore(tmp_path / "results")

    result = store.execute(f"SELECT * FROM {record.sql_name}", [record])

    assert result["status"] == "succeeded"
    assert result["row_count"] == 2
    assert len(result["warnings"]) == 1
    assert "could not be parsed" in result["warnings"][0]
    assert store.summary(result["result_handle"])["warnings"] == result["warnings"]


def test_clean_csv_produces_no_warning(tmp_path: Path) -> None:
    record = source(tmp_path)
    store = ResultStore(tmp_path / "results")

    result = store.execute(f"SELECT * FROM {record.sql_name}", [record])

    assert result["status"] == "succeeded"
    assert result["warnings"] == []


def test_invalid_result_handle_is_rejected(tmp_path: Path) -> None:
    store = ResultStore(tmp_path / "results")

    with pytest.raises(ValueError, match="Invalid result_handle"):
        store.page("../../outside")
