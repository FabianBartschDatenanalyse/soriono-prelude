from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from soriono_prelude.catalog import state_dir


@dataclass
class SourceRecord:
    source_handle: str
    resource_id: str
    title: str
    source_url: str
    duckdb_reader: str
    format: str | None = None
    access_method: str | None = None
    columns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    sql_name: str | None = None
    resolver_type: str | None = None
    resolver_config: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.sql_name:
            self.sql_name = sql_name_for(self.source_handle)
        if not self.resolver_type:
            from soriono_prelude.geodata_resolvers import infer_resolver

            self.resolver_type, self.resolver_config = infer_resolver(self.source_url)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def source_handle(resource_id: str, source_url: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", resource_id).strip("_").lower()[:70] or "source"
    digest = hashlib.sha256(f"{resource_id}\n{source_url}".encode()).hexdigest()[:10]
    return f"source:{slug}:{digest}"


def sql_name_for(handle: str) -> str:
    digest = hashlib.sha256(handle.encode()).hexdigest()[:12]
    return f"src_{digest}"


def source_payload(record: SourceRecord) -> dict[str, Any]:
    payload = {
        "source_handle": record.source_handle,
        "resource_id": record.resource_id,
        "title": record.title,
        "sql_name": record.sql_name,
        "format": record.format,
        "access_method": record.access_method,
        "columns": record.columns,
        "metadata": record.metadata,
    }
    if record.source_url.startswith(("https://", "http://")) and not record.metadata.get("private"):
        payload["source_url"] = record.source_url
    return payload


def reader_for(profile: dict[str, Any], source_url: str) -> str:
    source = profile.get("source") or {}
    stored_reader = str(source.get("duckdb_reader") or "")
    stored_url = str(source.get("source_url") or "")
    fmt = str(source.get("format") or "").lower()
    if fmt in {"pdf", "doc", "docx", "odt", "rtf", "html", "htm"}:
        raise ValueError(
            "Document resources are not SQL tables. Use the document tools."
        )
    if stored_reader and source_url == stored_url:
        return stored_reader
    access_method = str(source.get("access_method") or "").lower()
    url = source_url.lower()
    literal = sql_literal(source_url)
    if access_method == "pxweb_api":
        raise ValueError("PXWeb resources must be materialized before SQL execution")
    if fmt in {"parquet", "pq"} or ".parquet" in url:
        return f"read_parquet({literal})"
    if fmt in {"json", "geojson"} or ".json" in url or ".geojson" in url:
        return f"read_json_auto({literal})"
    if fmt == "xlsx" or ".xlsx" in url:
        return f"read_xlsx({literal})"
    if fmt == "xls" or url.split("?", 1)[0].endswith(".xls"):
        raise ValueError(
            "Legacy XLS workbooks cannot be read by DuckDB. "
            "Use the publisher's XLSX or CSV distribution instead."
        )
    if fmt in {"gpkg", "shp", "kml"}:
        return f"ST_Read({literal})"
    if fmt in {"csv", "tsv", "txt", "text"} or url.endswith(
        (".csv", ".tsv", ".txt")
    ):
        return f"read_csv_auto({literal}, store_rejects=true)"
    raise ValueError(
        f"Unsupported tabular format: {fmt or 'unknown'}. "
        "Use a format-specific materializer."
    )


class SourceRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or state_dir() / "sources.json"
        self.records: dict[str, SourceRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in payload.get("sources") or []:
            try:
                record = SourceRecord(**item)
            except (TypeError, ValueError):
                continue
            self.records[record.source_handle] = record

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"sources": [asdict(record) for record in self.list()]}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list(self) -> list[SourceRecord]:
        return sorted(self.records.values(), key=lambda item: item.source_handle)

    def get(self, handle: str) -> SourceRecord:
        try:
            return self.records[handle]
        except KeyError as exc:
            raise KeyError(f"Unknown source_handle: {handle}") from exc

    def register_profile(self, profile: dict[str, Any]) -> SourceRecord | None:
        source = profile.get("source") or {}
        active_url = str(source.get("source_url") or "").strip()
        if not active_url or str(source.get("access_method")) == "pxweb_api":
            return None
        try:
            reader = reader_for(profile, active_url)
        except ValueError:
            return None
        record = SourceRecord(
            source_handle=source_handle(str(profile["resource_id"]), active_url),
            resource_id=str(profile["resource_id"]),
            title=str(profile.get("title") or profile["resource_id"]),
            source_url=active_url,
            duckdb_reader=reader,
            format=str(source.get("format") or "") or None,
            access_method=str(source.get("access_method") or "") or None,
            columns=[str(item) for item in profile.get("columns") or []],
            metadata={"readiness": profile.get("readiness") or {}},
            resolver_type=str(source.get("resolver_type") or "") or None,
            resolver_config={
                str(key): str(value)
                for key, value in (source.get("resolver_config") or {}).items()
            },
        )
        return self._store(record)

    def register_materialized(
        self,
        profile: dict[str, Any],
        *,
        parquet_path: Path,
        columns: list[str],
        metadata: dict[str, Any],
    ) -> SourceRecord:
        url = parquet_path.resolve().as_posix()
        record = SourceRecord(
            source_handle=source_handle(str(profile["resource_id"]), url),
            resource_id=str(profile["resource_id"]),
            title=str(profile.get("title") or profile["resource_id"]),
            source_url=url,
            duckdb_reader=f"read_parquet({sql_literal(url)})",
            format="parquet",
            access_method="materialized_pxweb",
            columns=columns,
            metadata=metadata,
        )
        return self._store(record)

    def _store(self, record: SourceRecord) -> SourceRecord:
        existing = self.records.get(record.source_handle)
        if existing == record:
            return existing
        self.records[record.source_handle] = record
        self.save()
        return record
