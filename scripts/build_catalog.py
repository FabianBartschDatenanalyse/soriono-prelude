from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

FIELD_SET = (
    "resource_profile:doc_id,projection_version,source_hash,resource_id,title,publisher,"
    "source_system,access_method,format,format_family,landing_page_url,api_url,download_url,"
    "dimensions,measures,columns,geo_levels,time_from,time_to,years,units,semantic_warnings,"
    "content_type,analytical_suitability,resource_flags,truncation_notes,value_summaries_json,"
    "search_text,dimension_text,measure_text,sample_value_text,warning_text"
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the standalone Soriono Prelude SQLite catalog.")
    parser.add_argument("--vespa-url", default="http://localhost:8080")
    parser.add_argument("--namespace", default="soriono")
    parser.add_argument("--readiness-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("catalog/resources.sqlite"))
    args = parser.parse_args()
    result = build_catalog(
        vespa_url=args.vespa_url,
        namespace=args.namespace,
        readiness_report=args.readiness_report.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False))


def build_catalog(
    *,
    vespa_url: str,
    namespace: str,
    readiness_report: Path,
    output: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    readiness_payload = json.loads(readiness_report.read_text(encoding="utf-8"))
    readiness_rows = readiness_payload.get("rows") or []
    readiness = {
        str(row["resource_id"]): row
        for row in readiness_rows
        if isinstance(row, dict) and row.get("resource_id")
    }
    documents = _visit_profiles(vespa_url, namespace)
    if output.exists():
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(output)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE profiles (
                resource_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                publisher TEXT,
                source_system TEXT,
                format TEXT,
                workflow_smoke_passed INTEGER NOT NULL,
                profile_json TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE profiles_fts USING fts5(
                resource_id UNINDEXED,
                title,
                publisher,
                search_text,
                dimension_text,
                measure_text,
                sample_value_text,
                join_keys,
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        profile_rows = []
        fts_rows = []
        counts = {
            "retrievable": 0,
            "hydrated": 0,
            "duckdb_readable": 0,
            "materializable": 0,
            "join_keys_detected": 0,
            "workflow_smoke_passed": 0,
        }
        for fields in documents:
            resource_id = str(fields.get("resource_id") or "")
            if not resource_id:
                continue
            ready = readiness.get(resource_id, {})
            profile = _profile(fields, ready)
            for key in counts:
                counts[key] += int(bool(profile["readiness"].get(key)))
            profile_rows.append(
                (
                    resource_id,
                    str(profile.get("title") or resource_id),
                    str(profile.get("publisher") or ""),
                    str(profile.get("source_system") or ""),
                    str((profile.get("source") or {}).get("format") or ""),
                    int(bool(profile["readiness"].get("workflow_smoke_passed"))),
                    json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
                )
            )
            fts_rows.append(
                (
                    resource_id,
                    str(fields.get("title") or ""),
                    str(fields.get("publisher") or ""),
                    str(fields.get("search_text") or ""),
                    str(fields.get("dimension_text") or ""),
                    str(fields.get("measure_text") or ""),
                    str(fields.get("sample_value_text") or ""),
                    " ".join(str(item) for item in ready.get("join_keys") or []),
                )
            )
        connection.executemany("INSERT INTO profiles VALUES (?, ?, ?, ?, ?, ?, ?)", profile_rows)
        connection.executemany("INSERT INTO profiles_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", fts_rows)
        created_at = datetime.now(UTC).isoformat()
        metadata = {
            "catalog_version": created_at[:10],
            "created_at": created_at,
            "source": "opendata.swiss and BFS PXWeb profiles exported from Vespa",
            "resource_count": len(profile_rows),
            "counts": counts,
            "readiness_snapshot": readiness_payload.get("created_at"),
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
        )
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    manifest = {
        **metadata,
        "path": str(output),
        "size_bytes": output.stat().st_size,
        "sha256": digest,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
    (output.parent / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _visit_profiles(vespa_url: str, namespace: str) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    continuation: str | None = None
    with httpx.Client(timeout=120) as client:
        while True:
            params: dict[str, Any] = {
                "wantedDocumentCount": 1024,
                "fieldSet": FIELD_SET,
            }
            if continuation:
                params["continuation"] = continuation
            response = client.get(
                f"{vespa_url.rstrip('/')}/document/v1/{namespace}/resource_profile/docid",
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
            documents.extend(
                item.get("fields") or {}
                for item in payload.get("documents") or []
                if isinstance(item, dict)
            )
            continuation = payload.get("continuation")
            if not continuation:
                return documents


def _profile(fields: dict[str, Any], ready: dict[str, Any]) -> dict[str, Any]:
    access_method = str(fields.get("access_method") or ready.get("access_method") or "")
    source_record = ready.get("source") if isinstance(ready.get("source"), dict) else {}
    api_url = fields.get("api_url")
    download_url = fields.get("download_url")
    if access_method == "pxweb_api":
        source_url = None
        duckdb_reader = None
    else:
        source_url = source_record.get("source_url") or download_url
        duckdb_reader = source_record.get("duckdb_reader")
        if source_url and _looks_local(str(source_url)):
            source_url = download_url
            duckdb_reader = None
    value_summaries: dict[str, Any] = {}
    raw_values = fields.get("value_summaries_json")
    if isinstance(raw_values, str) and raw_values.strip():
        try:
            parsed = json.loads(raw_values)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            value_summaries = parsed
    readiness = {
        key: bool(ready.get(key))
        for key in (
            "retrievable",
            "hydrated",
            "duckdb_readable",
            "materializable",
            "join_keys_detected",
            "workflow_smoke_passed",
        )
    }
    return {
        "resource_id": fields.get("resource_id"),
        "title": fields.get("title"),
        "publisher": fields.get("publisher"),
        "source_system": fields.get("source_system"),
        "content_type": fields.get("content_type"),
        "analytical_suitability": fields.get("analytical_suitability"),
        "resource_flags": list(fields.get("resource_flags") or []),
        "dimensions": list(fields.get("dimensions") or []),
        "measures": list(fields.get("measures") or []),
        "columns": list(fields.get("columns") or source_record.get("columns") or []),
        "geo_levels": list(fields.get("geo_levels") or []),
        "time_from": fields.get("time_from"),
        "time_to": fields.get("time_to"),
        "years": [int(year) for year in fields.get("years") or []],
        "units": list(fields.get("units") or []),
        "join_keys": [str(item) for item in ready.get("join_keys") or []],
        "semantic_warnings": list(fields.get("semantic_warnings") or []),
        "value_summaries": value_summaries,
        "readiness": readiness,
        "blocked_reasons": list(ready.get("blocked_reasons") or []),
        "source": {
            "access_method": access_method,
            "format": fields.get("format") or ready.get("format"),
            "format_family": fields.get("format_family") or ready.get("format_family"),
            "landing_page_url": fields.get("landing_page_url"),
            "api_url": api_url,
            "source_url": source_url,
            "duckdb_reader": duckdb_reader,
        },
    }


def _looks_local(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("c:/", "d:/", "/home/", "/users/", "file:"))


if __name__ == "__main__":
    main()
