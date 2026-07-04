from __future__ import annotations

from soriono_prelude.reproduction import reproduction_bundle
from soriono_prelude.sources import SourceRecord


def test_reproduction_bundle_documents_steps_and_sql() -> None:
    source = SourceRecord(
        source_handle="source:test",
        resource_id="table-1",
        title="Bevölkerung",
        source_url="https://example.test/data.csv",
        duckdb_reader="read_csv_auto('https://example.test/data.csv')",
        sql_name="src_test",
    )
    bundle = reproduction_bundle(
        question="Wie viele Einwohner?",
        steps=["Quelle gesucht", "Tabelle aggregiert"],
        sql="SELECT SUM(population) FROM src_test",
        sources=[source],
        documents=[
            {
                "resource_id": "document-1",
                "title": "Methodenbericht",
                "format": "PDF",
                "page_count": 12,
                "content_sha256": "abc123",
            }
        ],
        result={"result_handle": "result:abc", "row_count": 1},
    )

    markdown = bundle["markdown"]
    assert markdown.startswith("## Vorgehen und Reproduktion")
    assert "SELECT SUM(population)" in markdown
    assert "document-1" in markdown
    assert "abc123" in markdown
    assert "result:abc" in markdown


def test_reproduction_bundle_supports_no_sql() -> None:
    bundle = reproduction_bundle(
        question="Was ist Prelude?",
        sources=[],
        steps=["Lokalen Kontext ausgewertet"],
    )
    assert bundle["sql"] is None
    assert "Lokalen Kontext ausgewertet" in bundle["markdown"]
