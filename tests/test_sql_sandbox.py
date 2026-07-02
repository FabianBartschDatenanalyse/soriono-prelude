from __future__ import annotations

from pathlib import Path

import pytest

from soriono_prelude.sources import SourceRecord
from soriono_prelude.sql import execute_sql, validate_sql


@pytest.fixture
def source(tmp_path: Path) -> SourceRecord:
    path = tmp_path / "allowed.csv"
    path.write_text("id,value\n1,allowed\n", encoding="utf-8")
    url = path.resolve().as_posix()
    return SourceRecord(
        source_handle="source:allowed",
        resource_id="allowed",
        title="Allowed",
        source_url=url,
        duckdb_reader=f"read_csv_auto('{url}')",
        format="csv",
        access_method="test",
    )


@pytest.mark.parametrize(
    "expression",
    [
        "read_text('C:/Windows/win.ini')",
        "read_blob('C:/Windows/win.ini')",
        "read_csv_auto('https://example.invalid/data.csv')",
        "read_json_auto('https://example.invalid/data.json')",
        "read_parquet('https://example.invalid/data.parquet')",
        "read_xlsx('C:/private.xlsx')",
        "glob('C:/*')",
        "sqlite_scan('C:/private.sqlite', 'data')",
        "postgres_scan('host=example.invalid', 'public', 'data')",
        "range(100)",
        "query('SELECT 1')",
        "duckdb_secrets()",
    ],
)
def test_all_client_table_functions_are_rejected(
    source: SourceRecord,
    expression: str,
) -> None:
    sql = f"SELECT * FROM {source.sql_name} CROSS JOIN {expression}"

    validation = validate_sql(sql, [source])

    assert validation["valid"] is False
    assert any(issue["code"] == "reader_functions_forbidden" for issue in validation["issues"])


def test_nested_ctes_unions_comments_and_case_are_allowed(source: SourceRecord) -> None:
    sql = (
        f"/* reviewed */ WITH Base AS (SELECT * FROM {source.sql_name}), "
        "Filtered AS (SELECT id, value FROM Base WHERE id = 1) "
        "SELECT * FROM Filtered UNION ALL SELECT * FROM Filtered"
    )

    validation = validate_sql(sql, [source])
    result = execute_sql(sql, [source])

    assert validation["valid"] is True
    assert result["status"] == "succeeded"
    assert result["row_count"] == 2


def test_unknown_alias_and_qualified_table_are_rejected(source: SourceRecord) -> None:
    unknown = validate_sql("SELECT * FROM src_not_registered", [source])
    qualified = validate_sql(f"SELECT * FROM main.{source.sql_name}", [source])

    assert any(issue["code"] == "source_not_allowed" for issue in unknown["issues"])
    assert any(issue["code"] == "source_not_allowed" for issue in qualified["issues"])


def test_reader_bypass_cannot_read_unregistered_local_file(
    source: SourceRecord,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("SENSITIVE_TEST_MARKER", encoding="utf-8")
    sql = (
        f"SELECT leaked.content FROM {source.sql_name} allowed "
        f"CROSS JOIN read_text('{secret.as_posix()}') leaked"
    )

    result = execute_sql(sql, [source])

    assert result["status"] == "failed"
    assert any(
        issue["code"] == "reader_functions_forbidden"
        for issue in result["validation"]["issues"]
    )
    assert result["rows"] == []
