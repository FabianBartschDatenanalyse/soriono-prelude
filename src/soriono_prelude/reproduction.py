from __future__ import annotations

from typing import Any

from soriono_prelude.sources import SourceRecord, source_payload


def reproduction_bundle(
    *,
    question: str,
    sql: str | None = None,
    sources: list[SourceRecord],
    steps: list[str] | None = None,
    documents: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_rows = [source_payload(source) for source in sources]
    lines = [
        "## Vorgehen und Reproduktion",
        "",
        f"**Frage:** {question}",
    ]
    if steps:
        lines.extend(["", "**Vorgehen:**", ""])
        lines.extend(
            f"{index}. {_line(step)}"
            for index, step in enumerate(steps, start=1)
        )
    if sources:
        lines.extend(
            [
                "",
                "**Datenquellen:**",
                "",
                "| Resource | Title | SQL name |",
                "|---|---|---|",
            ]
        )
        for source in sources:
            lines.append(
                f"| `{_cell(source.resource_id)}` | {_cell(source.title)} | "
                f"`{_cell(source.sql_name)}` |"
            )
    if documents:
        lines.extend(
            [
                "",
                "**Dokumentquellen:**",
                "",
                "| Resource | Title | Format | Seite(n) | SHA-256 |",
                "|---|---|---|---|---|",
            ]
        )
        for document in documents:
            lines.append(
                f"| `{_cell(document.get('resource_id'))}` | "
                f"{_cell(document.get('title'))} | "
                f"{_cell(document.get('format'))} | "
                f"{_cell(document.get('page_count'))} | "
                f"`{_cell(document.get('content_sha256'))}` |"
            )
    if sql and sql.strip():
        lines.extend(["", "**SQL:**", "", "```sql", sql.strip(), "```"])
    if result:
        lines.extend(
            [
                "",
                f"**Resultat:** `{_line(result.get('result_handle'))}`"
                f", {int(result.get('row_count') or 0)} Zeilen",
            ]
        )
    if not steps and not sources and not documents and not (sql and sql.strip()):
        lines.extend(
            [
                "",
                "Keine Datenquelle oder Berechnung verwendet; "
                "die Antwort basiert nur auf dem angegebenen Kontext.",
            ]
        )
    return {
        "question": question,
        "steps": list(steps or []),
        "sql": sql,
        "sources": source_rows,
        "documents": list(documents or []),
        "result": result,
        "rows_preview": list(rows or [])[:20],
        "markdown": "\n".join(lines),
    }


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _line(value: Any) -> str:
    return str(value or "").replace("\n", " ").strip()
