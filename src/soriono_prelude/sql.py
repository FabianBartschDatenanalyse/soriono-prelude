from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import expressions as exp

from soriono_prelude.sources import SourceRecord

FORBIDDEN_FUNCTIONS = {
    "duckdb_secrets",
    "getenv",
    "glob",
    "http_get",
    "query",
    "query_table",
    "read_blob",
    "read_text",
    "which_secret",
}
# Statement-level constructs that can never appear inside a read-only query.
# The names are resolved defensively so sqlglot version differences do not
# crash validation; unknown names are simply skipped.
_FORBIDDEN_NODE_NAMES = (
    "Alter",
    "Attach",
    "Command",
    "Copy",
    "Create",
    "Delete",
    "Detach",
    "Drop",
    "Export",
    "Grant",
    "Insert",
    "Kill",
    "LoadData",
    "Merge",
    "Pragma",
    "Set",
    "Transaction",
    "TruncateTable",
    "Update",
    "Use",
)
FORBIDDEN_NODES = tuple(
    node
    for node in (getattr(exp, name, None) for name in _FORBIDDEN_NODE_NAMES)
    if node is not None
)


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
        for node in statement.find_all(*FORBIDDEN_NODES):
            issues.append(
                {
                    "code": "not_read_only",
                    "message": f"SQL contains a forbidden operation: {node.key.upper()}",
                }
            )
        for select in statement.find_all(exp.Select):
            if select.args.get("into"):
                issues.append(
                    {
                        "code": "not_read_only",
                        "message": "SELECT INTO is not allowed in read-only SQL.",
                    }
                )
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


def _function_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        return str(function.this).casefold()
    return function.sql_name().casefold()
