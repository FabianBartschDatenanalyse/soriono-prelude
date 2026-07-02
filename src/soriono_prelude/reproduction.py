from __future__ import annotations

from typing import Any

from soriono_prelude.sources import SourceRecord, source_payload


def reproduction_bundle(
    *,
    question: str,
    sql: str,
    sources: list[SourceRecord],
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_rows = [source_payload(source) for source in sources]
    lines = [
        "# Reproduction bundle",
        "",
        f"**Question:** {question}",
        "",
        "## Sources",
        "",
        "| Resource | Title | SQL name |",
        "|---|---|---|",
    ]
    for source in sources:
        lines.append(
            f"| `{_cell(source.resource_id)}` | {_cell(source.title)} | "
            f"`{_cell(source.sql_name)}` |"
        )
    lines.extend(["", "## SQL", "", "```sql", sql.strip(), "```"])
    return {
        "question": question,
        "sql": sql,
        "sources": source_rows,
        "rows_preview": list(rows or [])[:20],
        "markdown": "\n".join(lines),
    }


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")
