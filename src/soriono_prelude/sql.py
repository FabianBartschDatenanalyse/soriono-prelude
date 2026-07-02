from __future__ import annotations

import re
import time
from typing import Any

import sqlglot
from sqlglot import expressions as exp

from soriono_prelude.duckdb_runtime import open_connection
from soriono_prelude.sources import SourceRecord

FORBIDDEN_SQL = re.compile(
    r"\b(attach|call|copy|create|delete|detach|drop|export|import|insert|install|load|merge|pragma|"
    r"replace|set|truncate|update|vacuum)\b",
    re.IGNORECASE,
)
FORBIDDEN_FUNCTIONS = {
    "duckdb_secrets",
    "getenv",
    "glob",
    "http_get",
    "query",
    "read_blob",
    "read_text",
    "which_secret",
}


def validate_sql(sql: str, sources: list[SourceRecord]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    text = (sql or "").strip()
    if not text:
        return {"valid": False, "issues": [{"code": "sql_missing", "message": "SQL must not be empty."}]}
    try:
        statements = sqlglot.parse(text, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        return {
            "valid": False,
            "issues": [{"code": "sql_parse_failed", "message": str(exc)[:1000]}],
        }
    if len(statements) != 1:
        issues.append({"code": "multiple_statements", "message": "Exactly one SQL statement is allowed."})
    root = statements[0] if statements else None
    if root is None or not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        issues.append({"code": "not_read_only", "message": "SQL must be a read-only SELECT or WITH query."})
    if FORBIDDEN_SQL.search(text):
        issues.append({"code": "forbidden_operation", "message": "SQL contains a forbidden operation."})

    supplied = {str(source.sql_name).casefold(): source for source in sources}
    cte_names = {
        cte.alias_or_name.casefold()
        for statement in statements
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    referenced_sources: set[str] = set()
    reader_function_found = False
    for statement in statements:
        for table in statement.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                reader_function_found = True
                continue
            name = table.name.casefold()
            if table.db or table.catalog:
                issues.append(
                    {
                        "code": "source_not_allowed",
                        "message": f"Qualified table references are not allowed: {table.sql()}",
                    }
                )
            elif name in supplied:
                referenced_sources.add(name)
            elif name not in cte_names:
                issues.append(
                    {
                        "code": "source_not_allowed",
                        "message": f"Unknown table alias: {table.name}",
                        "allowed_sql_names": sorted(supplied),
                    }
                )
        for function in statement.find_all(exp.Func):
            name = _function_name(function)
            if name in FORBIDDEN_FUNCTIONS or name.startswith("read_") or name.endswith("_scan"):
                reader_function_found = True
    if reader_function_found:
        issues.append(
            {
                "code": "reader_functions_forbidden",
                "message": (
                    "Client SQL may not call table readers or external-access functions. "
                    "Use registered sql_name aliases."
                ),
                "allowed_sql_names": sorted(supplied),
            }
        )
    if not referenced_sources:
        issues.append(
            {
                "code": "registered_source_unused",
                "message": "SQL must reference at least one supplied sql_name.",
                "allowed_sql_names": sorted(supplied),
            }
        )
    return {
        "valid": not issues,
        "issues": issues,
        "allowed_sources": [
            {
                "source_handle": source.source_handle,
                "resource_id": source.resource_id,
                "sql_name": source.sql_name,
            }
            for source in sources
        ],
    }


def execute_sql(
    sql: str,
    sources: list[SourceRecord],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    validation = validate_sql(sql, sources)
    if not validation["valid"]:
        return {"status": "failed", "validation": validation, "rows": [], "columns": []}
    executable = sql.rstrip().rstrip(";")
    if limit is not None:
        executable = f"SELECT * FROM ({executable}) AS soriono_result LIMIT {max(0, int(limit))}"
    started = time.perf_counter()
    try:
        connection = open_connection(sources)
        try:
            result = connection.execute(executable)
            columns = [item[0] for item in result.description or []]
            rows = [dict(zip(columns, row, strict=False)) for row in result.fetchall()]
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "validation": validation,
            "error": str(exc)[:2000],
            "exception_type": exc.__class__.__name__,
            "rows": [],
            "columns": [],
        }
    return {
        "status": "succeeded",
        "validation": validation,
        "execution_ms": int((time.perf_counter() - started) * 1000),
        "row_count": len(rows),
        "columns": columns,
        "rows": rows,
    }


def _function_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        return str(function.this).casefold()
    return function.sql_name().casefold()
