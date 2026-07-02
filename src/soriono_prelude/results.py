from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any

import duckdb

from soriono_prelude.catalog import state_dir
from soriono_prelude.duckdb_runtime import open_connection
from soriono_prelude.sources import SourceRecord, sql_literal
from soriono_prelude.sql import validate_sql

DEFAULT_INLINE_ROWS = 200
DEFAULT_MAX_PAGE_ROWS = 500
DEFAULT_MAX_RESULT_BYTES = 1_000_000_000
DEFAULT_TTL_HOURS = 168
HANDLE_PREFIX = "result:"


class ResultStore:
    def __init__(
        self,
        root: Path | None = None,
        *,
        inline_rows: int | None = None,
        max_page_rows: int | None = None,
        max_result_bytes: int | None = None,
        ttl_hours: int | None = None,
    ) -> None:
        self.root = (root or state_dir() / "results").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.inline_rows = max(
            0,
            inline_rows
            if inline_rows is not None
            else int(os.environ.get("SORIONO_PRELUDE_RESULT_INLINE_ROWS", DEFAULT_INLINE_ROWS)),
        )
        self.max_page_rows = max(
            1,
            max_page_rows
            if max_page_rows is not None
            else int(os.environ.get("SORIONO_PRELUDE_RESULT_MAX_PAGE_ROWS", DEFAULT_MAX_PAGE_ROWS)),
        )
        self.max_result_bytes = max(
            1,
            max_result_bytes
            if max_result_bytes is not None
            else int(os.environ.get("SORIONO_PRELUDE_RESULT_MAX_BYTES", DEFAULT_MAX_RESULT_BYTES)),
        )
        self.ttl_hours = max(
            1,
            ttl_hours
            if ttl_hours is not None
            else int(os.environ.get("SORIONO_PRELUDE_RESULT_TTL_HOURS", DEFAULT_TTL_HOURS)),
        )
        self.cleanup_expired()

    def execute(
        self,
        sql: str,
        sources: list[SourceRecord],
        *,
        limit: int | None = None,
        excluded_columns: list[str] | None = None,
        private: bool = False,
        inline_rows: int | None = None,
    ) -> dict[str, Any]:
        self.cleanup_expired()
        validation = validate_sql(sql, sources)
        if not validation["valid"]:
            return {
                "status": "failed",
                "validation": validation,
                "rows": [],
                "columns": [],
            }

        executable = sql.rstrip().rstrip(";")
        if limit is not None:
            executable = (
                f"SELECT * FROM ({executable}) AS soriono_limited_result "
                f"LIMIT {max(0, int(limit))}"
            )

        result_id = uuid.uuid4().hex
        handle = f"{HANDLE_PREFIX}{result_id}"
        temporary_path = self.root / f".{result_id}.parquet.tmp"
        parquet_path = self.root / f"{result_id}.parquet"
        metadata_path = self.root / f"{result_id}.json"
        started = perf_counter()
        removed = {column.casefold() for column in excluded_columns or []}

        connection = open_connection(sources)
        try:
            schema_rows = connection.execute(
                f"DESCRIBE SELECT * FROM ({executable}) AS soriono_described_result"
            ).fetchall()
            visible_columns = [str(row[0]) for row in schema_rows if str(row[0]).casefold() not in removed]
            if not visible_columns:
                return {
                    "status": "failed",
                    "validation": validation,
                    "error": "The result has no columns that may be persisted.",
                    "exception_type": "EmptyVisibleResult",
                    "rows": [],
                    "columns": [],
                }
            projection = ", ".join(_identifier(column) for column in visible_columns)
            connection.execute(
                f"COPY (SELECT {projection} FROM ({executable}) AS soriono_persisted_result) "
                f"TO {sql_literal(temporary_path.resolve().as_posix())} "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        except Exception as exc:  # noqa: BLE001
            temporary_path.unlink(missing_ok=True)
            return {
                "status": "failed",
                "validation": validation,
                "error": str(exc)[:2000],
                "exception_type": exc.__class__.__name__,
                "rows": [],
                "columns": [],
            }
        finally:
            connection.close()

        size_bytes = temporary_path.stat().st_size
        current_storage_bytes = sum(path.stat().st_size for path in self.root.glob("*.parquet"))
        if size_bytes > self.max_result_bytes or current_storage_bytes + size_bytes > self.max_result_bytes:
            temporary_path.unlink(missing_ok=True)
            return {
                "status": "result_too_large",
                "validation": validation,
                "error": "The local result store exceeds the configured storage limit.",
                "size_bytes": size_bytes,
                "current_storage_bytes": current_storage_bytes,
                "maximum_size_bytes": self.max_result_bytes,
                "rows": [],
                "columns": [],
            }
        os.replace(temporary_path, parquet_path)

        preview_limit = self.inline_rows if inline_rows is None else max(0, min(int(inline_rows), self.inline_rows))
        row_count, columns, rows = _inspect_result(parquet_path, offset=0, limit=preview_limit)
        created_at = dt.datetime.now(dt.UTC)
        metadata = {
            "result_handle": handle,
            "created_at": created_at.isoformat(),
            "expires_at": (created_at + dt.timedelta(hours=self.ttl_hours)).isoformat(),
            "row_count": row_count,
            "columns": columns,
            "size_bytes": size_bytes,
            "sql": sql,
            "source_handles": [source.source_handle for source in sources],
            "resource_ids": [source.resource_id for source in sources],
            "private": bool(private),
            "removed_columns": sorted(removed),
        }
        _write_json_atomic(metadata_path, metadata)
        returned_count = len(rows)
        return {
            "status": "succeeded",
            "validation": validation,
            "execution_ms": int((perf_counter() - started) * 1000),
            "result_handle": handle,
            "row_count": row_count,
            "returned_count": returned_count,
            "truncated": returned_count < row_count,
            "columns": [column["name"] for column in columns],
            "rows": rows,
            "size_bytes": size_bytes,
            "expires_at": metadata["expires_at"],
            "removed_columns": metadata["removed_columns"],
        }

    def page(self, result_handle: str, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        metadata, parquet_path = self._load(result_handle)
        active_offset = max(0, int(offset))
        active_limit = max(1, min(int(limit), self.max_page_rows))
        _, columns, rows = _inspect_result(
            parquet_path,
            offset=active_offset,
            limit=active_limit,
            include_count=False,
        )
        returned_count = len(rows)
        next_offset = active_offset + returned_count
        has_more = next_offset < int(metadata["row_count"])
        return {
            "status": "succeeded",
            "result_handle": result_handle,
            "row_count": int(metadata["row_count"]),
            "offset": active_offset,
            "limit": active_limit,
            "returned_count": returned_count,
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
            "columns": [column["name"] for column in columns],
            "rows": rows,
        }

    def summary(self, result_handle: str) -> dict[str, Any]:
        metadata, _ = self._load(result_handle)
        return {
            "status": "succeeded",
            **metadata,
        }

    def analysis_source(self, result_handle: str) -> SourceRecord:
        metadata, parquet_path = self._load(result_handle)
        path = parquet_path.as_posix()
        return SourceRecord(
            source_handle=f"result-source:{result_handle.removeprefix(HANDLE_PREFIX)}",
            resource_id=result_handle,
            title=f"Stored result {result_handle}",
            source_url=path,
            duckdb_reader=f"read_parquet({sql_literal(path)})",
            format="parquet",
            access_method="stored_result",
            columns=[str(column["name"]) for column in metadata["columns"]],
            metadata={"private": bool(metadata.get("private")), "result_handle": result_handle},
        )

    def small_count_violations(
        self,
        result_handle: str,
        *,
        count_columns: set[str],
        minimum: int,
        maximum_violations: int = 20,
    ) -> list[dict[str, Any]]:
        metadata, parquet_path = self._load(result_handle)
        candidates = [
            str(column["name"])
            for column in metadata["columns"]
            if str(column["name"]).casefold() in count_columns
        ]
        violations: list[dict[str, Any]] = []
        connection = duckdb.connect(":memory:")
        try:
            for column in candidates:
                quoted = _identifier(column)
                rows = connection.execute(
                    f"SELECT {quoted} FROM read_parquet(?) "
                    f"WHERE {quoted} > 0 AND {quoted} < ? LIMIT ?",
                    [str(parquet_path), int(minimum), max(1, int(maximum_violations))],
                ).fetchall()
                violations.extend({"column": column, "value": _json_safe(row[0])} for row in rows)
                if len(violations) >= maximum_violations:
                    break
        finally:
            connection.close()
        return violations[:maximum_violations]

    def remove_for_resource(self, resource_id: str) -> int:
        removed = 0
        for metadata_path in self.root.glob("*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if resource_id in metadata.get("resource_ids", []):
                self.remove(str(metadata["result_handle"]))
                removed += 1
        return removed

    def remove(self, result_handle: str) -> None:
        result_id = _result_id(result_handle)
        (self.root / f"{result_id}.parquet").unlink(missing_ok=True)
        (self.root / f"{result_id}.json").unlink(missing_ok=True)

    def cleanup_expired(self) -> int:
        now = dt.datetime.now(dt.UTC)
        removed = 0
        for metadata_path in self.root.glob("*.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                expires_at = dt.datetime.fromisoformat(str(metadata["expires_at"]))
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if expires_at <= now:
                self.remove(str(metadata["result_handle"]))
                removed += 1
        return removed

    def _load(self, result_handle: str) -> tuple[dict[str, Any], Path]:
        result_id = _result_id(result_handle)
        metadata_path = self.root / f"{result_id}.json"
        parquet_path = self.root / f"{result_id}.parquet"
        if not metadata_path.is_file() or not parquet_path.is_file():
            raise KeyError(f"Unknown or expired result_handle: {result_handle}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return metadata, parquet_path


def _result_id(result_handle: str) -> str:
    value = str(result_handle)
    if not value.startswith(HANDLE_PREFIX):
        raise ValueError("Invalid result_handle")
    result_id = value.removeprefix(HANDLE_PREFIX)
    if len(result_id) != 32 or any(character not in "0123456789abcdef" for character in result_id):
        raise ValueError("Invalid result_handle")
    return result_id


def _inspect_result(
    path: Path,
    *,
    offset: int,
    limit: int,
    include_count: bool = True,
) -> tuple[int, list[dict[str, str]], list[dict[str, Any]]]:
    connection = duckdb.connect(":memory:")
    try:
        path_value = str(path)
        schema_rows = connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [path_value]).fetchall()
        row_count = (
            int(connection.execute("SELECT COUNT(*) FROM read_parquet(?)", [path_value]).fetchone()[0])
            if include_count
            else 0
        )
        result = connection.execute(
            "SELECT * FROM read_parquet(?) LIMIT ? OFFSET ?",
            [path_value, max(0, int(limit)), max(0, int(offset))],
        )
        names = [str(item[0]) for item in result.description or []]
        rows = [
            {name: _json_safe(value) for name, value in zip(names, row, strict=False)}
            for row in result.fetchall()
        ]
    finally:
        connection.close()
    columns = [{"name": str(row[0]), "type": str(row[1])} for row in schema_rows]
    return row_count, columns, rows


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime, dt.time, decimal.Decimal, Path)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value
