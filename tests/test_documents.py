from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from soriono_prelude.documents import (
    DocumentStore,
    ExtractedSection,
    _chunk_sections,
    _extract_docx,
    _extract_html,
    _extract_rtf,
    _validate_public_http_url,
)


def _document(
    resource_id: str = "document-1",
    *,
    source_url: str = "https://example.test/report.pdf",
    source_modified: str = "2025-01-01",
) -> dict[str, object]:
    return {
        "resource_id": resource_id,
        "package_id": "package-1",
        "dataset_name": "social-report",
        "dataset_title": "Sozialbericht",
        "title": "Sozialbericht Köniz 2025",
        "description": "Bevölkerung, Wohnen und soziale Sicherheit",
        "publisher": "Gemeinde Köniz",
        "source_system": "test",
        "format": "PDF",
        "source_url": source_url,
        "landing_page_url": "https://example.test/report",
        "language": '["de"]',
        "media_type": "application/pdf",
        "byte_size": 123,
        "source_modified": source_modified,
        "metadata_json": "{}",
        "indexed_at": "2026-07-04T00:00:00Z",
    }


def test_seed_catalog_is_copied_to_user_state(tmp_path: Path) -> None:
    seed = tmp_path / "seed.sqlite"
    seed_store = DocumentStore(seed)
    with seed_store.connect() as connection:
        seed_store._upsert_metadata(connection, _document())

    state = tmp_path / "state" / "documents.sqlite"
    copied = DocumentStore(state, seed_path=seed)

    assert copied.status()["resource_count"] == 1
    assert copied.get("document-1")["dataset_title"] == "Sozialbericht"


def test_empty_legacy_state_is_replaced_with_seed_catalog(tmp_path: Path) -> None:
    seed = tmp_path / "seed.sqlite"
    seed_store = DocumentStore(seed)
    with seed_store.connect() as connection:
        seed_store._upsert_metadata(connection, _document())

    state = tmp_path / "state" / "documents.sqlite"
    state.parent.mkdir()
    with sqlite3.connect(state) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                resource_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                publisher TEXT NOT NULL DEFAULT '',
                format TEXT NOT NULL,
                source_url TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                resource_id UNINDEXED,
                title,
                description,
                publisher
            );
            """
        )

    migrated = DocumentStore(state, seed_path=seed)

    assert migrated.status()["resource_count"] == 1
    assert migrated.search("Sozialbericht")[0]["resource_id"] == "document-1"


def test_nonempty_legacy_state_is_migrated_without_data_loss(tmp_path: Path) -> None:
    state = tmp_path / "documents.sqlite"
    with sqlite3.connect(state) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                resource_id TEXT PRIMARY KEY,
                package_id TEXT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                publisher TEXT NOT NULL DEFAULT '',
                source_system TEXT NOT NULL DEFAULT 'opendata.swiss',
                format TEXT NOT NULL,
                source_url TEXT NOT NULL,
                landing_page_url TEXT,
                language TEXT,
                media_type TEXT,
                byte_size INTEGER,
                source_modified TEXT,
                metadata_json TEXT NOT NULL,
                indexed_at TEXT NOT NULL,
                extraction_status TEXT NOT NULL DEFAULT 'not_materialized',
                extraction_method TEXT,
                content_sha256 TEXT,
                page_count INTEGER,
                character_count INTEGER,
                local_path TEXT,
                warnings_json TEXT NOT NULL DEFAULT '[]',
                extracted_at TEXT
            );
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                resource_id UNINDEXED,
                title,
                description,
                publisher
            );
            INSERT INTO documents(
                resource_id, title, description, publisher, format, source_url,
                metadata_json, indexed_at
            ) VALUES (
                'legacy-1', 'Alter Bericht', 'Historische Daten', 'Bund',
                'PDF', 'https://example.test/legacy.pdf', '{}', '2025-01-01'
            );
            INSERT INTO documents_fts(
                rowid, resource_id, title, description, publisher
            ) VALUES (1, 'legacy-1', 'Alter Bericht', 'Historische Daten', 'Bund');
            """
        )

    migrated = DocumentStore(state)

    assert migrated.status()["resource_count"] == 1
    assert migrated.get("legacy-1")["dataset_title"] == "Alter Bericht"
    assert migrated.search("Historische Daten")[0]["resource_id"] == "legacy-1"


def test_document_metadata_and_body_are_searchable(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "documents.sqlite")
    with store.connect() as connection:
        store._upsert_metadata(connection, _document())
        cursor = connection.execute(
            "INSERT INTO document_chunks(resource_id, ordinal, page_number, heading, text) "
            "VALUES (?, ?, ?, ?, ?)",
            ("document-1", 0, 7, "Armut", "Die Armutsquote wird nach Haushalten ausgewiesen."),
        )
        connection.execute(
            "INSERT INTO document_chunks_fts(rowid, resource_id, heading, text) "
            "VALUES (?, ?, ?, ?)",
            (
                cursor.lastrowid,
                "document-1",
                "Armut",
                "Die Armutsquote wird nach Haushalten ausgewiesen.",
            ),
        )
        connection.execute(
            "UPDATE documents SET extraction_status = 'extracted', warnings_json = ? "
            "WHERE resource_id = ?",
            (json.dumps([]), "document-1"),
        )

    assert store.search("soziale Sicherheit")[0]["resource_id"] == "document-1"
    body_hit = store.search("Armutsquote")[0]
    assert body_hit["match_page"] == 7
    assert store.read("document-1", query="Haushalte")["chunks"][0]["page_number"] == 7


def test_changed_source_invalidates_extracted_content(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "documents.sqlite")
    with store.connect() as connection:
        store._upsert_metadata(connection, _document())
        cursor = connection.execute(
            "INSERT INTO document_chunks(resource_id, ordinal, text) VALUES (?, ?, ?)",
            ("document-1", 0, "old text"),
        )
        connection.execute(
            "INSERT INTO document_chunks_fts(rowid, resource_id, heading, text) "
            "VALUES (?, ?, ?, ?)",
            (cursor.lastrowid, "document-1", "", "old text"),
        )
        connection.execute(
            "UPDATE documents SET extraction_status = 'extracted' WHERE resource_id = ?",
            ("document-1",),
        )
        store._upsert_metadata(
            connection,
            _document(
                source_url="https://example.test/new.pdf",
                source_modified="2026-01-01",
            ),
        )

    assert store.get("document-1")["extraction_status"] == "not_materialized"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0] == 0


def test_download_failure_returns_structured_error_and_marks_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DocumentStore(tmp_path / "documents.sqlite")
    with store.connect() as connection:
        store._upsert_metadata(connection, _document())

    def failing_download(document: dict[str, object]) -> tuple:
        raise ValueError("Document exceeds 1 byte download limit")

    monkeypatch.setattr(store, "_download", failing_download)

    result = store.materialize("document-1")

    assert result["status"] == "failed"
    assert "download limit" in result["error"]
    document = store.get("document-1")
    assert document["extraction_status"] == "failed"
    assert any("download limit" in warning for warning in document["warnings"])


def test_docx_html_and_rtf_extractors(tmp_path: Path) -> None:
    docx = tmp_path / "sample.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Titel</w:t></w:r></w:p>
                <w:p><w:r><w:t>Inhalt des Berichts</w:t></w:r></w:p>
              </w:body>
            </w:document>""",
        )
    sections = _extract_docx(docx)
    assert sections[0].heading == "Titel"
    assert sections[1].text == "Inhalt des Berichts"

    html = tmp_path / "sample.html"
    html.write_text(
        "<html><nav>Menü</nav><main><h1>Titel</h1><p>Nutztext</p></main></html>",
        encoding="utf-8",
    )
    assert "Nutztext" in _extract_html(html)
    assert "Menü" not in _extract_html(html)

    rtf = tmp_path / "sample.rtf"
    rtf.write_text(r"{\rtf1 Bericht\par Zweite Zeile}", encoding="latin-1")
    assert "Zweite Zeile" in _extract_rtf(rtf)


def test_archive_bomb_ratio_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SORIONO_PRELUDE_MAX_ARCHIVE_COMPRESSION_RATIO", "2")
    docx = tmp_path / "bomb.docx"
    with zipfile.ZipFile(docx, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"A" * 2_000_000)

    with pytest.raises(ValueError, match="compression ratio"):
        _extract_docx(docx)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/private",
        "http://127.0.0.1/private",
        "http://169.254.169.254/latest/meta-data",
        "http://user:password@example.com/document.pdf",
    ],
)
def test_private_or_privileged_document_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        _validate_public_http_url(url)


def test_chunking_preserves_page_numbers_and_limits() -> None:
    chunks = _chunk_sections(
        [ExtractedSection("Absatz " * 1_000, page_number=4, heading="Kapitel")],
        target_characters=500,
        overlap_characters=50,
    )
    assert len(chunks) > 1
    assert {chunk["page_number"] for chunk in chunks} == {4}
    assert {chunk["heading"] for chunk in chunks} == {"Kapitel"}


def test_document_database_has_standard_integrity(tmp_path: Path) -> None:
    store = DocumentStore(tmp_path / "documents.sqlite")
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
