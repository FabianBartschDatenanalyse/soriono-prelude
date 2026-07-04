from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
from pypdf import PdfReader

from soriono_prelude import USER_AGENT
from soriono_prelude.catalog import product_root, query_terms, state_dir

DOCUMENT_FORMATS = ("PDF", "DOC", "DOCX", "ODT", "RTF", "HTML", "HTM")
CKAN_PACKAGE_SEARCH = "https://ckan.opendata.swiss/api/3/action/package_search"
CKAN_PACKAGE_SHOW = "https://ckan.opendata.swiss/api/3/action/package_show"
CKAN_RESOURCE_SEARCH = "https://ckan.opendata.swiss/api/3/action/resource_search"
SPACE_RE = re.compile(r"[ \t]+")
BLANK_RE = re.compile(r"\n{3,}")
REDIRECT_LIMIT = 5
DEFAULT_DOWNLOAD_LIMIT = 50 * 1024 * 1024
DEFAULT_ARCHIVE_MEMBER_LIMIT = 20 * 1024 * 1024
DEFAULT_ARCHIVE_TOTAL_LIMIT = 100 * 1024 * 1024
DEFAULT_ARCHIVE_MEMBER_COUNT = 5_000
DEFAULT_ARCHIVE_RATIO_LIMIT = 200


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    page_number: int | None = None
    heading: str | None = None


class DocumentStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        seed_path: Path | None = None,
    ) -> None:
        explicit_path = path is not None
        self.path = (path or state_dir() / "documents.sqlite").resolve()
        self.seed_path = (
            seed_path
            if seed_path is not None
            else (None if explicit_path else product_root() / "catalog" / "documents.sqlite")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() and self.seed_path and self.seed_path.exists():
            self._copy_seed()
        self.cache_dir = self.path.parent / "documents"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if self._is_empty() and self._seed_has_documents():
            self._copy_seed()
            self._initialize()

    def _copy_seed(self) -> None:
        if not self.seed_path or not self.seed_path.exists():
            return
        if self.seed_path.resolve() == self.path:
            return
        with (
            closing(
                sqlite3.connect(
                    f"file:{self.seed_path.resolve().as_posix()}?mode=ro",
                    uri=True,
                )
            ) as source,
            closing(sqlite3.connect(self.path)) as target,
        ):
            source.backup(target)
            target.commit()

    def _is_empty(self) -> bool:
        with closing(sqlite3.connect(self.path)) as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            ) == 0

    def _seed_has_documents(self) -> bool:
        if not self.seed_path or not self.seed_path.exists():
            return False
        if self.seed_path.resolve() == self.path:
            return False
        with closing(
            sqlite3.connect(
                f"file:{self.seed_path.resolve().as_posix()}?mode=ro",
                uri=True,
            )
        ) as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            ) > 0

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
            ).fetchone()
            if existing:
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(documents)")
                }
                if "dataset_name" not in columns:
                    connection.execute("ALTER TABLE documents ADD COLUMN dataset_name TEXT")
                if "dataset_title" not in columns:
                    connection.execute(
                        "ALTER TABLE documents ADD COLUMN "
                        "dataset_title TEXT NOT NULL DEFAULT ''"
                    )
                    connection.execute(
                        "UPDATE documents SET dataset_title = title "
                        "WHERE dataset_title = ''"
                    )

                fts_definition = connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'documents_fts'"
                ).fetchone()
                if fts_definition and "dataset_title" not in str(fts_definition["sql"]):
                    connection.execute("DROP TABLE documents_fts")

            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    resource_id TEXT PRIMARY KEY,
                    package_id TEXT,
                    dataset_name TEXT,
                    dataset_title TEXT NOT NULL DEFAULT '',
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
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    resource_id UNINDEXED,
                    title,
                    dataset_title,
                    description,
                    publisher,
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TABLE IF NOT EXISTS document_chunks (
                    chunk_id INTEGER PRIMARY KEY,
                    resource_id TEXT NOT NULL REFERENCES documents(resource_id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    page_number INTEGER,
                    heading TEXT,
                    text TEXT NOT NULL,
                    UNIQUE(resource_id, ordinal)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    resource_id UNINDEXED,
                    heading,
                    text,
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE INDEX IF NOT EXISTS document_chunks_resource
                    ON document_chunks(resource_id, ordinal);
                """
            )
            document_count = int(
                connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            )
            fts_count = int(
                connection.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
            )
            if document_count != fts_count:
                connection.execute("DELETE FROM documents_fts")
                connection.execute(
                    "INSERT INTO documents_fts("
                    "rowid, resource_id, title, dataset_title, description, publisher"
                    ") SELECT rowid, resource_id, title, dataset_title, description, publisher "
                    "FROM documents"
                )

    def status(self) -> dict[str, Any]:
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
            extracted = int(
                connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE extraction_status = 'extracted'"
                ).fetchone()[0]
            )
            formats = {
                str(row["format"]): int(row["count"])
                for row in connection.execute(
                    "SELECT format, COUNT(*) AS count FROM documents GROUP BY format ORDER BY format"
                )
            }
        return {
            "path": str(self.path),
            "resource_count": total,
            "extracted_count": extracted,
            "formats": formats,
        }

    def sync_opendata_swiss(
        self,
        *,
        formats: list[str] | None = None,
        page_size: int = 100,
    ) -> dict[str, Any]:
        selected = [_normalize_document_format(item) for item in (formats or DOCUMENT_FORMATS)]
        invalid = sorted(set(selected) - set(DOCUMENT_FORMATS))
        if invalid:
            raise ValueError(f"Unsupported document formats: {', '.join(invalid)}")
        selected = list(dict.fromkeys(selected))
        query_formats = [item for item in selected if item != "HTM"]
        query = " OR ".join(f"res_format:{item}" for item in query_formats)
        now = _now()
        start = 0
        seen: set[str] = set()
        package_cache: dict[str, dict[str, Any]] = {}
        with self.connect() as connection:
            while True:
                page = _ckan_package_page(query, start=start, rows=page_size)
                packages = page["results"]
                for package in packages:
                    package_cache[str(package.get("id") or "")] = package
                    for document in _package_documents(package, selected=selected, now=now):
                        self._upsert_metadata(connection, document)
                        seen.add(str(document["resource_id"]))
                connection.commit()
                start += len(packages)
                if not packages or start >= page["count"]:
                    break
            for document_format in [
                item for item in selected if item not in {"PDF", "HTML", "HTM"}
            ]:
                offset = 0
                while True:
                    page = _ckan_resource_page(
                        document_format,
                        offset=offset,
                        limit=1_000,
                    )
                    resources = page["results"]
                    for resource in resources:
                        package_id = str(resource.get("package_id") or "")
                        package = package_cache.get(package_id)
                        if package is None:
                            package = _ckan_package_show(package_id)
                            package_cache[package_id] = package
                        package_resource = next(
                            (
                                item
                                for item in package.get("resources") or []
                                if str(item.get("id")) == str(resource.get("id"))
                            ),
                            {},
                        )
                        enriched_package = {
                            **package,
                            "resources": [{**package_resource, **resource}],
                        }
                        candidates = _package_documents(
                            enriched_package,
                            selected=selected,
                            now=now,
                        )
                        document = next(
                            (
                                item
                                for item in candidates
                                if str(item["resource_id"]) == str(resource.get("id"))
                            ),
                            None,
                        )
                        if document is None:
                            continue
                        self._upsert_metadata(connection, document)
                        seen.add(str(document["resource_id"]))
                    connection.commit()
                    offset += len(resources)
                    if not resources or offset >= page["count"]:
                        break
            placeholders = ",".join("?" for _ in selected)
            stale = connection.execute(
                f"SELECT resource_id FROM documents WHERE source_system = 'opendata.swiss' "
                f"AND format IN ({placeholders})",
                selected,
            ).fetchall()
            stale_ids = [str(row["resource_id"]) for row in stale if str(row["resource_id"]) not in seen]
            for resource_id in stale_ids:
                self._delete_document(connection, resource_id)
            connection.commit()
            placeholders = ",".join("?" for _ in selected)
            counts = dict.fromkeys(selected, 0)
            counts.update(
                {
                    str(row["format"]): int(row["count"])
                    for row in connection.execute(
                        f"SELECT format, COUNT(*) AS count FROM documents "
                        f"WHERE format IN ({placeholders}) GROUP BY format",
                        selected,
                    )
                }
            )
        return {
            "status": "synchronized",
            "source": "opendata.swiss",
            "formats": counts,
            "removed_count": len(stale_ids),
            "resource_count": self.status()["resource_count"],
        }

    def _delete_document(self, connection: sqlite3.Connection, resource_id: str) -> None:
        connection.execute(
            "DELETE FROM document_chunks_fts WHERE resource_id = ?",
            (resource_id,),
        )
        connection.execute("DELETE FROM documents_fts WHERE resource_id = ?", (resource_id,))
        connection.execute("DELETE FROM documents WHERE resource_id = ?", (resource_id,))

    def _upsert_metadata(
        self,
        connection: sqlite3.Connection,
        document: dict[str, Any],
    ) -> None:
        existing = connection.execute(
            "SELECT source_url, source_modified, extraction_status, extraction_method, "
            "content_sha256, page_count, character_count, local_path, warnings_json, extracted_at "
            "FROM documents WHERE resource_id = ?",
            (document["resource_id"],),
        ).fetchone()
        unchanged = bool(
            existing
            and str(existing["source_url"]) == str(document["source_url"])
            and str(existing["source_modified"] or "") == str(document["source_modified"] or "")
        )
        retained = dict(existing) if existing and unchanged else {}
        if existing and not unchanged:
            connection.execute(
                "DELETE FROM document_chunks_fts WHERE resource_id = ?",
                (document["resource_id"],),
            )
            connection.execute(
                "DELETE FROM document_chunks WHERE resource_id = ?",
                (document["resource_id"],),
            )
        values = {
            **document,
            "extraction_status": retained.get("extraction_status", "not_materialized"),
            "extraction_method": retained.get("extraction_method"),
            "content_sha256": retained.get("content_sha256"),
            "page_count": retained.get("page_count"),
            "character_count": retained.get("character_count"),
            "local_path": retained.get("local_path"),
            "warnings_json": retained.get("warnings_json", "[]"),
            "extracted_at": retained.get("extracted_at"),
        }
        connection.execute(
            """
            INSERT INTO documents (
                resource_id, package_id, dataset_name, dataset_title, title,
                description, publisher, source_system, format, source_url,
                landing_page_url, language, media_type, byte_size, source_modified,
                metadata_json, indexed_at, extraction_status, extraction_method,
                content_sha256, page_count, character_count, local_path,
                warnings_json, extracted_at
            ) VALUES (
                :resource_id, :package_id, :dataset_name, :dataset_title, :title,
                :description, :publisher, :source_system, :format, :source_url,
                :landing_page_url, :language, :media_type, :byte_size, :source_modified,
                :metadata_json, :indexed_at, :extraction_status, :extraction_method,
                :content_sha256, :page_count, :character_count, :local_path,
                :warnings_json, :extracted_at
            )
            ON CONFLICT(resource_id) DO UPDATE SET
                package_id=excluded.package_id,
                dataset_name=excluded.dataset_name,
                dataset_title=excluded.dataset_title,
                title=excluded.title,
                description=excluded.description,
                publisher=excluded.publisher,
                source_system=excluded.source_system,
                format=excluded.format,
                source_url=excluded.source_url,
                landing_page_url=excluded.landing_page_url,
                language=excluded.language,
                media_type=excluded.media_type,
                byte_size=excluded.byte_size,
                source_modified=excluded.source_modified,
                metadata_json=excluded.metadata_json,
                indexed_at=excluded.indexed_at,
                extraction_status=excluded.extraction_status,
                extraction_method=excluded.extraction_method,
                content_sha256=excluded.content_sha256,
                page_count=excluded.page_count,
                character_count=excluded.character_count,
                local_path=excluded.local_path,
                warnings_json=excluded.warnings_json,
                extracted_at=excluded.extracted_at
            """,
            values,
        )
        connection.execute("DELETE FROM documents_fts WHERE resource_id = ?", (document["resource_id"],))
        connection.execute(
            "INSERT INTO documents_fts(resource_id, title, dataset_title, description, publisher) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                document["resource_id"],
                document["title"],
                document["dataset_title"],
                document["description"],
                document["publisher"],
            ),
        )

    def get(self, resource_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE resource_id = ?",
                (resource_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown document resource_id: {resource_id}")
        return _public_document(row)

    def search(
        self,
        question: str,
        *,
        top_k: int = 20,
        format: str | None = None,
        materialized_only: bool = False,
    ) -> list[dict[str, Any]]:
        terms = query_terms(question)
        if not terms:
            return []
        match = " OR ".join(f'"{term}"*' for term in terms)
        limit = max(1, min(int(top_k), 100))
        metadata_parameters: list[Any] = [match]
        metadata_filters: list[str] = []
        if format:
            metadata_filters.append("d.format = ?")
            metadata_parameters.append(_normalize_document_format(format))
        if materialized_only:
            metadata_filters.append("d.extraction_status = 'extracted'")
        metadata_suffix = "".join(f" AND {item}" for item in metadata_filters)
        metadata_parameters.append(limit)
        chunk_parameters: list[Any] = [match]
        chunk_filters: list[str] = []
        if format:
            chunk_filters.append("d.format = ?")
            chunk_parameters.append(_normalize_document_format(format))
        if materialized_only:
            chunk_filters.append("d.extraction_status = 'extracted'")
        chunk_suffix = "".join(f" AND {item}" for item in chunk_filters)
        chunk_parameters.append(limit * 3)
        with self.connect() as connection:
            metadata = connection.execute(
                f"""
                SELECT d.*, bm25(documents_fts, 0.0, 8.0, 6.0, 3.0, 2.0) AS rank,
                       snippet(documents_fts, 3, '[', ']', ' … ', 24) AS snippet
                FROM documents_fts
                JOIN documents AS d ON d.resource_id = documents_fts.resource_id
                WHERE documents_fts MATCH ? {metadata_suffix}
                ORDER BY rank LIMIT ?
                """,
                metadata_parameters,
            ).fetchall()
            chunks = connection.execute(
                f"""
                SELECT d.*, bm25(document_chunks_fts, 0.0, 3.0, 1.0) AS rank,
                       snippet(document_chunks_fts, 2, '[', ']', ' … ', 32) AS snippet,
                       c.page_number AS match_page
                FROM document_chunks_fts
                JOIN document_chunks AS c ON c.chunk_id = document_chunks_fts.rowid
                JOIN documents AS d ON d.resource_id = c.resource_id
                WHERE document_chunks_fts MATCH ? {chunk_suffix}
                ORDER BY rank LIMIT ?
                """,
                chunk_parameters,
            ).fetchall()
        combined: dict[str, dict[str, Any]] = {}
        for matched_in, rows in (("metadata", metadata), ("content", chunks)):
            for rank, row in enumerate(rows, start=1):
                resource_id = str(row["resource_id"])
                item = combined.setdefault(
                    resource_id,
                    {
                        **_public_document(row),
                        "score": 0.0,
                        "matched_in": [],
                        "snippet": str(row["snippet"] or ""),
                    },
                )
                item["score"] += 1.0 / (60.0 + rank)
                item["matched_in"] = list(dict.fromkeys([*item["matched_in"], matched_in]))
                if matched_in == "content":
                    item["snippet"] = str(row["snippet"] or "")
                    item["match_page"] = row["match_page"]
        return sorted(combined.values(), key=lambda item: item["score"], reverse=True)[:limit]

    def materialize(
        self,
        resource_id: str,
        *,
        force: bool = False,
        ocr: bool = True,
    ) -> dict[str, Any]:
        document = self.get(resource_id)
        if document["extraction_status"] == "extracted" and not force:
            return {"status": "cached", "document": document}
        warnings: list[str] = []
        try:
            source_path, digest, byte_size, content_type = self._download(document)
            sections, method, page_count, extraction_warnings = _extract_document(
                source_path,
                str(document["format"]),
                ocr=ocr,
            )
            warnings.extend(extraction_warnings)
            chunks = _chunk_sections(sections)
            character_count = sum(len(chunk["text"]) for chunk in chunks)
            status = "extracted" if chunks else "empty"
            if not chunks:
                warnings.append("No readable text was extracted.")
            with self.connect() as connection:
                connection.execute(
                    "DELETE FROM document_chunks_fts WHERE resource_id = ?",
                    (resource_id,),
                )
                connection.execute(
                    "DELETE FROM document_chunks WHERE resource_id = ?",
                    (resource_id,),
                )
                for ordinal, chunk in enumerate(chunks):
                    cursor = connection.execute(
                        "INSERT INTO document_chunks(resource_id, ordinal, page_number, heading, text) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            resource_id,
                            ordinal,
                            chunk["page_number"],
                            chunk["heading"],
                            chunk["text"],
                        ),
                    )
                    connection.execute(
                        "INSERT INTO document_chunks_fts(rowid, resource_id, heading, text) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            cursor.lastrowid,
                            resource_id,
                            chunk["heading"] or "",
                            chunk["text"],
                        ),
                    )
                connection.execute(
                    """
                    UPDATE documents
                    SET extraction_status = ?, extraction_method = ?, content_sha256 = ?,
                        page_count = ?, character_count = ?, local_path = ?, byte_size = ?,
                        media_type = COALESCE(media_type, ?), warnings_json = ?, extracted_at = ?
                    WHERE resource_id = ?
                    """,
                    (
                        status,
                        method,
                        digest,
                        page_count,
                        character_count,
                        str(source_path),
                        byte_size,
                        content_type,
                        json.dumps(warnings, ensure_ascii=False),
                        _now(),
                        resource_id,
                    ),
                )
                connection.commit()
        except Exception as exc:
            with self.connect() as connection:
                connection.execute(
                    "UPDATE documents SET extraction_status = 'failed', warnings_json = ?, "
                    "extracted_at = ? WHERE resource_id = ?",
                    (json.dumps([str(exc)[:2000]], ensure_ascii=False), _now(), resource_id),
                )
                connection.commit()
            return {
                "status": "failed",
                "resource_id": resource_id,
                "error": str(exc)[:2000],
                "exception_type": exc.__class__.__name__,
            }
        return {
            "status": status,
            "document": self.get(resource_id),
            "chunk_count": len(chunks),
            "warnings": warnings,
        }

    def _download(self, document: dict[str, Any]) -> tuple[Path, str, int, str | None]:
        max_bytes = int(
            os.environ.get("SORIONO_PRELUDE_MAX_DOCUMENT_BYTES", DEFAULT_DOWNLOAD_LIMIT)
        )
        source_url = str(document["source_url"])
        _validate_public_http_url(source_url)
        temporary_name = hashlib.sha256(str(document["resource_id"]).encode()).hexdigest()[:24]
        temp_path = self.cache_dir / f".{temporary_name}.download"
        digest = hashlib.sha256()
        total = 0
        content_type = None

        def validate_request(request: httpx.Request) -> None:
            _validate_public_http_url(str(request.url))

        try:
            with httpx.Client(
                timeout=httpx.Timeout(120, connect=20),
                follow_redirects=True,
                max_redirects=REDIRECT_LIMIT,
                headers={"User-Agent": USER_AGENT},
                event_hooks={"request": [validate_request]},
            ) as client:
                with client.stream("GET", source_url) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";")[0] or None
                    declared_text = response.headers.get("content-length") or "0"
                    declared = int(declared_text) if declared_text.isdigit() else 0
                    if declared > max_bytes:
                        raise ValueError(f"Document exceeds {max_bytes} byte download limit")
                    with temp_path.open("wb") as output:
                        for block in response.iter_bytes(1024 * 1024):
                            total += len(block)
                            if total > max_bytes:
                                raise ValueError(
                                    f"Document exceeds {max_bytes} byte download limit"
                                )
                            digest.update(block)
                            output.write(block)
            _validate_document_signature(temp_path, str(document["format"]))
            target = self.cache_dir / f"{digest.hexdigest()}.{str(document['format']).casefold()}"
            if target.exists():
                temp_path.unlink(missing_ok=True)
            else:
                os.replace(temp_path, target)
            return target, digest.hexdigest(), total, content_type
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def read(
        self,
        resource_id: str,
        *,
        query: str | None = None,
        page_number: int | None = None,
        offset: int = 0,
        limit: int = 10,
        max_characters: int = 20_000,
    ) -> dict[str, Any]:
        document = self.get(resource_id)
        active_limit = max(1, min(int(limit), 50))
        active_offset = max(0, int(offset))
        active_characters = max(1_000, min(int(max_characters), 100_000))
        clauses = ["c.resource_id = ?"]
        parameters: list[Any] = [resource_id]
        join = ""
        order = "c.ordinal"
        if query:
            terms = query_terms(query)
            if terms:
                join = "JOIN document_chunks_fts ON document_chunks_fts.rowid = c.chunk_id"
                clauses.append("document_chunks_fts MATCH ?")
                parameters.append(" OR ".join(f'"{term}"*' for term in terms))
                order = "bm25(document_chunks_fts)"
        if page_number is not None:
            clauses.append("c.page_number = ?")
            parameters.append(int(page_number))
        parameters.extend((active_limit, active_offset))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT c.ordinal, c.page_number, c.heading, c.text
                FROM document_chunks AS c {join}
                WHERE {' AND '.join(clauses)}
                ORDER BY {order}
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        output: list[dict[str, Any]] = []
        used = 0
        truncated = False
        for row in rows:
            text = str(row["text"])
            remaining = active_characters - used
            if remaining <= 0:
                truncated = True
                break
            if len(text) > remaining:
                text = text[:remaining]
                truncated = True
            output.append(
                {
                    "ordinal": row["ordinal"],
                    "page_number": row["page_number"],
                    "heading": row["heading"],
                    "text": text,
                }
            )
            used += len(text)
        return {
            "document": document,
            "query": query,
            "page_number": page_number,
            "offset": active_offset,
            "returned_count": len(output),
            "characters": used,
            "truncated": truncated,
            "chunks": output,
        }


def _ckan_package_page(query: str, *, start: int, rows: int) -> dict[str, Any]:
    response = httpx.get(
        CKAN_PACKAGE_SEARCH,
        params={"q": query, "start": start, "rows": max(1, min(int(rows), 1_000))},
        timeout=90,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise RuntimeError("CKAN package_search failed")
    result = body["result"]
    return {"count": int(result["count"]), "results": list(result["results"])}


def _ckan_resource_page(
    document_format: str,
    *,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    response = httpx.get(
        CKAN_RESOURCE_SEARCH,
        params={
            "query": f"format:{document_format}",
            "offset": offset,
            "limit": max(1, min(int(limit), 1_000)),
        },
        timeout=90,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise RuntimeError(f"CKAN resource_search failed for {document_format}")
    result = body["result"]
    return {"count": int(result["count"]), "results": list(result["results"])}


def _ckan_package_show(package_id: str) -> dict[str, Any]:
    if not package_id:
        raise ValueError("Document resource has no package_id")
    response = httpx.get(
        CKAN_PACKAGE_SHOW,
        params={"id": package_id},
        timeout=60,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("success"):
        raise RuntimeError(f"CKAN package_show failed for {package_id}")
    return dict(body["result"])


def _package_documents(
    package: dict[str, Any],
    *,
    selected: list[str],
    now: str,
) -> list[dict[str, Any]]:
    package_id = str(package.get("id") or "")
    dataset_name = str(package.get("name") or "")
    dataset_title = _multilingual_value(package.get("title")) or dataset_name or package_id
    package_description = _multilingual_value(package.get("notes"))
    organization = package.get("organization") or {}
    publisher = _multilingual_value(
        organization.get("title") or organization.get("display_name") or organization.get("name")
    )
    landing_page = (
        f"https://opendata.swiss/de/dataset/{dataset_name}" if dataset_name else None
    )
    documents = []
    for resource in package.get("resources") or []:
        document_format = _resource_document_format(resource)
        if document_format not in selected:
            continue
        source_url = str(resource.get("download_url") or resource.get("url") or "").strip()
        if not source_url:
            continue
        resource_title = _multilingual_value(
            resource.get("title")
            or resource.get("name")
            or resource.get("display_name")
        )
        fallback_title = Path(urlparse(source_url).path).name
        resource_description = _multilingual_value(resource.get("description"))
        description_parts = list(
            dict.fromkeys(
                item for item in (resource_description, package_description) if item
            )
        )
        metadata = {
            "resource": resource,
            "package": {
                "id": package_id,
                "name": dataset_name,
                "title": package.get("title"),
                "organization": {
                    "name": organization.get("name"),
                    "title": organization.get("title"),
                },
                "metadata_modified": package.get("metadata_modified"),
            },
        }
        documents.append(
            {
                "resource_id": str(resource["id"]),
                "package_id": package_id,
                "dataset_name": dataset_name,
                "dataset_title": dataset_title,
                "title": resource_title or fallback_title or dataset_title,
                "description": "\n\n".join(description_parts),
                "publisher": publisher,
                "source_system": "opendata.swiss",
                "format": document_format,
                "source_url": source_url,
                "landing_page_url": landing_page,
                "language": json.dumps(
                    _language_values(resource.get("language") or package.get("language")),
                    ensure_ascii=False,
                ),
                "media_type": str(
                    resource.get("media_type") or resource.get("mimetype") or ""
                )
                or None,
                "byte_size": _positive_integer(
                    resource.get("byte_size") or resource.get("size")
                ),
                "source_modified": str(
                    resource.get("modified")
                    or resource.get("last_modified")
                    or package.get("metadata_modified")
                    or ""
                ),
                "metadata_json": json.dumps(
                    metadata,
                    ensure_ascii=False,
                    default=str,
                    separators=(",", ":"),
                ),
                "indexed_at": now,
            }
        )
    return documents


def _multilingual_value(value: Any) -> str:
    if not value:
        return ""
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value.strip()
    if isinstance(decoded, dict):
        values = [decoded.get(key) for key in ("de", "fr", "it", "en")]
        return " | ".join(dict.fromkeys(str(item).strip() for item in values if item))
    return str(decoded).strip()


def _language_values(value: Any) -> list[str]:
    if not value:
        return []
    decoded = value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = [value]
    if isinstance(decoded, dict):
        decoded = list(decoded)
    if not isinstance(decoded, list):
        decoded = [decoded]
    return list(dict.fromkeys(str(item).strip() for item in decoded if str(item).strip()))


def _positive_integer(value: Any) -> int | None:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _normalize_document_format(value: str) -> str:
    normalized = value.strip().upper()
    if normalized == "PDF" or normalized.endswith("/PDF"):
        return "PDF"
    if normalized in {
        "HTML",
        "HTM",
        "TEXT/HTML",
        "APPLICATION/XHTML+XML",
    } or normalized.endswith("/HTML"):
        return "HTML"
    if normalized in {"RTF", "APPLICATION/RTF", "TEXT/RTF"}:
        return "RTF"
    if normalized in {
        "DOCX",
        "APPLICATION/VND.OPENXMLFORMATS-OFFICEDOCUMENT.WORDPROCESSINGML.DOCUMENT",
    }:
        return "DOCX"
    if normalized in {"DOC", "APPLICATION/MSWORD"}:
        return "DOC"
    if normalized in {"ODT", "APPLICATION/VND.OASIS.OPENDOCUMENT.TEXT"}:
        return "ODT"
    return normalized


def _resource_document_format(resource: dict[str, Any]) -> str:
    path = urlparse(
        str(resource.get("download_url") or resource.get("url") or "")
    ).path.casefold()
    for suffix, document_format in (
        (".docx", "DOCX"),
        (".odt", "ODT"),
        (".rtf", "RTF"),
        (".doc", "DOC"),
        (".pdf", "PDF"),
        (".html", "HTML"),
        (".htm", "HTML"),
    ):
        if path.endswith(suffix):
            return document_format
    return _normalize_document_format(
        str(resource.get("format") or resource.get("media_type") or "")
    )


def _public_document(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    get = row.__getitem__
    return {
        "resource_id": get("resource_id"),
        "package_id": get("package_id"),
        "dataset_name": get("dataset_name"),
        "dataset_title": get("dataset_title"),
        "title": get("title"),
        "description": get("description"),
        "publisher": get("publisher"),
        "source_system": get("source_system"),
        "format": get("format"),
        "source_url": get("source_url"),
        "landing_page_url": get("landing_page_url"),
        "language": json.loads(str(get("language") or "[]")),
        "media_type": get("media_type"),
        "byte_size": get("byte_size"),
        "source_modified": get("source_modified"),
        "extraction_status": get("extraction_status"),
        "extraction_method": get("extraction_method"),
        "content_sha256": get("content_sha256"),
        "page_count": get("page_count"),
        "character_count": get("character_count"),
        "warnings": json.loads(str(get("warnings_json") or "[]")),
    }


def _validate_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Document URLs must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("Document URLs must not contain credentials")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Document URL has no hostname")
    if hostname.casefold() == "localhost" or hostname.casefold().endswith(".localhost"):
        raise ValueError("Local document hosts are forbidden")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = list(
                dict.fromkeys(
                    ipaddress.ip_address(item[4][0])
                    for item in socket.getaddrinfo(
                        hostname,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                        type=socket.SOCK_STREAM,
                    )
                )
            )
        except socket.gaierror as exc:
            raise ValueError(f"Document host cannot be resolved: {hostname}") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Private, local, reserved, or non-global document hosts are forbidden")


def _validate_document_signature(path: Path, document_format: str) -> None:
    head = path.read_bytes()[:16]
    active_format = _normalize_document_format(document_format)
    if active_format == "PDF" and not head.startswith(b"%PDF-"):
        raise ValueError("Downloaded content is not a PDF")
    if active_format in {"DOCX", "ODT"} and not head.startswith(b"PK"):
        raise ValueError(f"Downloaded content is not a valid {active_format} archive")
    if active_format == "DOC" and not head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        raise ValueError("Downloaded content is not a legacy DOC compound file")
    if active_format == "RTF" and not head.lstrip().startswith(b"{\\rtf"):
        raise ValueError("Downloaded content is not RTF")


def _safe_zip_read(archive: zipfile.ZipFile, member: str) -> bytes:
    infos = archive.infolist()
    max_members = int(
        os.environ.get("SORIONO_PRELUDE_MAX_ARCHIVE_MEMBERS", DEFAULT_ARCHIVE_MEMBER_COUNT)
    )
    max_member_bytes = int(
        os.environ.get(
            "SORIONO_PRELUDE_MAX_ARCHIVE_MEMBER_BYTES",
            DEFAULT_ARCHIVE_MEMBER_LIMIT,
        )
    )
    max_total_bytes = int(
        os.environ.get(
            "SORIONO_PRELUDE_MAX_ARCHIVE_TOTAL_BYTES",
            DEFAULT_ARCHIVE_TOTAL_LIMIT,
        )
    )
    max_ratio = int(
        os.environ.get(
            "SORIONO_PRELUDE_MAX_ARCHIVE_COMPRESSION_RATIO",
            DEFAULT_ARCHIVE_RATIO_LIMIT,
        )
    )
    if len(infos) > max_members:
        raise ValueError(f"Document archive has more than {max_members} members")
    if sum(info.file_size for info in infos) > max_total_bytes:
        raise ValueError(f"Document archive expands beyond {max_total_bytes} bytes")
    for info in infos:
        if info.flag_bits & 0x1:
            raise ValueError("Encrypted document archives are not supported")
        compressed = max(1, info.compress_size)
        if info.file_size > 1_000_000 and info.file_size / compressed > max_ratio:
            raise ValueError("Suspicious document archive compression ratio")
    try:
        info = archive.getinfo(member)
    except KeyError as exc:
        raise ValueError(f"Document archive is missing {member}") from exc
    if info.file_size > max_member_bytes:
        raise ValueError(f"Document XML member exceeds {max_member_bytes} bytes")
    return archive.read(info)


def _extract_document(
    path: Path,
    document_format: str,
    *,
    ocr: bool,
) -> tuple[list[ExtractedSection], str, int | None, list[str]]:
    active_format = _normalize_document_format(document_format)
    if active_format == "PDF":
        return _extract_pdf(path, ocr=ocr)
    if active_format == "DOCX":
        return _extract_docx(path), "docx_ooxml", None, []
    if active_format == "ODT":
        return _extract_odt(path), "odt_xml", None, []
    if active_format == "RTF":
        text = _extract_rtf(path)
        return [ExtractedSection(text)] if text else [], "rtf_plain_text", None, []
    if active_format in {"HTML", "HTM"}:
        text = _extract_html(path)
        return [ExtractedSection(text)] if text else [], "html_main_text", None, []
    if active_format == "DOC":
        return _extract_legacy_doc(path)
    raise ValueError(f"Unsupported document format: {document_format}")


def _extract_pdf(
    path: Path,
    *,
    ocr: bool,
) -> tuple[list[ExtractedSection], str, int, list[str]]:
    reader = PdfReader(path, strict=False)
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise ValueError("Encrypted PDF cannot be read")
    max_pages = int(os.environ.get("SORIONO_PRELUDE_MAX_DOCUMENT_PAGES", 500))
    if len(reader.pages) > max_pages:
        raise ValueError(f"PDF has {len(reader.pages)} pages; limit is {max_pages}")
    sections = [
        ExtractedSection(
            _clean_text(page.extract_text() or ""),
            page_number=index,
        )
        for index, page in enumerate(reader.pages, start=1)
    ]
    readable = sum(len(item.text) for item in sections)
    warnings: list[str] = []
    method = "pypdf"
    if readable < max(100, len(reader.pages) * 20):
        if ocr:
            ocr_sections = _ocr_pdf(path, len(reader.pages))
            if ocr_sections:
                sections = ocr_sections
                method = "pdftoppm+tesseract"
            else:
                warnings.append(
                    "PDF contains little extractable text and OCR tools are unavailable or failed."
                )
        else:
            warnings.append("PDF contains little extractable text; OCR was disabled.")
    return [item for item in sections if item.text], method, len(reader.pages), warnings


def _ocr_pdf(path: Path, page_count: int) -> list[ExtractedSection]:
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not pdftoppm or not tesseract:
        return []
    languages = os.environ.get("SORIONO_PRELUDE_OCR_LANGUAGES", "deu+fra+ita+eng")
    with tempfile.TemporaryDirectory(prefix="soriono-prelude-ocr-") as temp_dir:
        prefix = Path(temp_dir) / "page"
        subprocess.run(
            [pdftoppm, "-r", "200", "-png", str(path), str(prefix)],
            check=True,
            timeout=max(120, page_count * 30),
            capture_output=True,
        )
        sections = []
        for index, image in enumerate(sorted(Path(temp_dir).glob("page-*.png")), start=1):
            result = subprocess.run(
                [tesseract, str(image), "stdout", "-l", languages],
                check=True,
                timeout=120,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            text = _clean_text(result.stdout)
            if text:
                sections.append(ExtractedSection(text, page_number=index))
        return sections


def _extract_docx(path: Path) -> list[ExtractedSection]:
    with zipfile.ZipFile(path) as archive:
        xml = _safe_zip_read(archive, "word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    sections = []
    current_heading = None
    for paragraph in root.iter(f"{namespace}p"):
        text = _clean_text(
            "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        )
        if not text:
            continue
        style = paragraph.find(f".//{namespace}pStyle")
        style_name = style.attrib.get(f"{namespace}val", "") if style is not None else ""
        if style_name.casefold().startswith("heading"):
            current_heading = text
        sections.append(ExtractedSection(text, heading=current_heading))
    return sections


def _extract_odt(path: Path) -> list[ExtractedSection]:
    with zipfile.ZipFile(path) as archive:
        xml = _safe_zip_read(archive, "content.xml")
    root = ElementTree.fromstring(xml)
    sections = []
    current_heading = None
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name not in {"p", "h"}:
            continue
        text = _clean_text("".join(element.itertext()))
        if text:
            if local_name == "h":
                current_heading = text
            sections.append(ExtractedSection(text, heading=current_heading))
    return sections


def _extract_legacy_doc(
    path: Path,
) -> tuple[list[ExtractedSection], str, int | None, list[str]]:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("Legacy DOC extraction requires LibreOffice/soffice")
    with tempfile.TemporaryDirectory(prefix="soriono-prelude-doc-") as temp_dir:
        profile = Path(temp_dir) / "profile"
        output = Path(temp_dir) / "output"
        output.mkdir()
        subprocess.run(
            [
                soffice,
                "--headless",
                "--safe-mode",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "docx",
                "--outdir",
                str(output),
                str(path),
            ],
            check=True,
            timeout=120,
            capture_output=True,
        )
        converted = next(output.glob("*.docx"), None)
        if not converted:
            raise RuntimeError("LibreOffice did not produce a DOCX file")
        return _extract_docx(converted), "libreoffice-safe-mode+docx_ooxml", None, []


def _extract_rtf(path: Path) -> str:
    raw = path.read_text(encoding="latin-1", errors="replace")
    raw = re.sub(
        r"\\'[0-9a-fA-F]{2}",
        lambda match: bytes.fromhex(match.group()[2:]).decode("cp1252"),
        raw,
    )
    raw = re.sub(
        r"\\u(-?\d+)\??",
        lambda match: chr(int(match.group(1)) % 65_536),
        raw,
    )
    raw = re.sub(r"\\(?:par|line)\b ?", "\n", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
    raw = re.sub(r"[{}]", "", raw)
    return _clean_text(raw)


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in {"script", "style", "svg", "noscript", "form", "nav", "footer"}:
            self.hidden += 1
        elif not self.hidden and tag in {
            "p",
            "div",
            "article",
            "section",
            "h1",
            "h2",
            "h3",
            "li",
            "br",
            "tr",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "noscript", "form", "nav", "footer"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in {
            "p",
            "div",
            "article",
            "section",
            "h1",
            "h2",
            "h3",
            "li",
            "tr",
        }:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _extract_html(path: Path) -> str:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    if "\ufffd" in text:
        text = raw.decode("windows-1252", errors="replace")
    parser = _TextHTMLParser()
    parser.feed(text)
    return _clean_text(html.unescape(" ".join(parser.parts)))


def _chunk_sections(
    sections: list[ExtractedSection],
    *,
    target_characters: int = 4_000,
    overlap_characters: int = 400,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    max_total = int(
        os.environ.get("SORIONO_PRELUDE_MAX_DOCUMENT_CHARACTERS", 5_000_000)
    )
    total = 0
    for section in sections:
        text = _clean_text(section.text)
        start = 0
        while text and start < len(text) and total < max_total:
            end = min(len(text), start + target_characters)
            if end < len(text):
                boundary = text.rfind("\n", start, end)
                if boundary <= start + target_characters // 2:
                    boundary = text.rfind(" ", start, end)
                if boundary > start:
                    end = boundary
            value = text[start:end].strip()[: max_total - total]
            if value:
                chunks.append(
                    {
                        "page_number": section.page_number,
                        "heading": section.heading,
                        "text": value,
                    }
                )
                total += len(value)
            if end >= len(text):
                break
            start = max(start + 1, end - overlap_characters)
    return chunks


def _clean_text(value: str) -> str:
    lines = [
        SPACE_RE.sub(" ", line).strip()
        for line in str(value).replace("\r", "\n").split("\n")
    ]
    return BLANK_RE.sub(
        "\n\n",
        "\n".join(line for line in lines if line),
    ).strip()


def _now() -> str:
    return datetime.now(UTC).isoformat()
