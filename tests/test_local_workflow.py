from __future__ import annotations

from pathlib import Path

from soriono_prelude.sources import SourceRecord, SourceRegistry
from soriono_prelude.sql import execute_sql, validate_sql


def local_source(tmp_path: Path) -> SourceRecord:
    csv_path = tmp_path / "observations.csv"
    csv_path.write_text(
        "municipality,year,x,y,group\n"
        "A,2020,1,3,left\n"
        "A,2021,2,5,left\n"
        "B,2020,3,7,right\n"
        "B,2021,4,9,right\n"
        "C,2021,5,11,right\n",
        encoding="utf-8",
    )
    source_url = csv_path.resolve().as_posix()
    return SourceRecord(
        source_handle="source:test:local",
        resource_id="test-local-data",
        title="Local test observations",
        source_url=source_url,
        duckdb_reader=f"read_csv_auto('{source_url}')",
        format="csv",
        access_method="local_test",
    )


def test_registry_sql_and_join(tmp_path: Path) -> None:
    record = local_source(tmp_path)
    registry = SourceRegistry(path=tmp_path / "sources.json")
    registry.records[record.source_handle] = record
    registry.save()
    assert SourceRegistry(path=registry.path).get(record.source_handle) == record
    sql = (
        f"WITH base AS (SELECT * FROM {record.sql_name}), "
        "years AS (SELECT year, COUNT(*) AS n FROM base GROUP BY year) "
        "SELECT b.municipality, b.year, y.n FROM base b JOIN years y USING (year) ORDER BY 1, 2"
    )

    assert validate_sql(sql, [record])["valid"] is True
    result = execute_sql(sql, [record])
    assert result["status"] == "succeeded"
    assert result["row_count"] == 5
    assert result["rows"][0] == {"municipality": "A", "year": 2020, "n": 2}


def test_sql_rejects_empty_and_unregistered_sources(tmp_path: Path) -> None:
    record = local_source(tmp_path)

    assert validate_sql("", [record])["issues"][0]["code"] == "sql_missing"
    result = validate_sql("SELECT * FROM read_csv_auto('C:/not-registered.csv')", [record])
    assert result["valid"] is False
    assert any(issue["code"] == "reader_functions_forbidden" for issue in result["issues"])
