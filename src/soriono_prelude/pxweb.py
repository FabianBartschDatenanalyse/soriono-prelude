from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Any

import duckdb
import httpx

from soriono_prelude.catalog import state_dir

DEFAULT_CELL_LIMIT = 100_000
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_RETRY_ATTEMPTS = 2
RETRY_STATUS = {429, 500, 502, 503, 504}


class PxWebCubeTooLarge(RuntimeError):
    def __init__(self, cell_count: int, cell_limit: int) -> None:
        self.cell_count = cell_count
        self.cell_limit = cell_limit
        super().__init__(f"PXWeb request has {cell_count} cells; limit is {cell_limit}. Supply a narrower scope.")


class PxWebUnknownDimensions(ValueError):
    def __init__(self, unknown: list[str], available: list[str]) -> None:
        self.unknown = unknown
        self.available = available
        self.suggestions = {
            item: get_close_matches(item, available, n=3, cutoff=0.5)
            for item in unknown
        }
        super().__init__(
            f"Unknown PXWeb dimensions: {unknown}. Available dimensions: {available}"
        )


@dataclass(frozen=True)
class MaterializedCube:
    parquet_path: Path
    row_count: int
    columns: list[str]
    cell_count: int
    cached: bool
    scoped: bool


def materialize_pxweb(
    api_url: str,
    *,
    scope: dict[str, list[str]] | None = None,
    force: bool = False,
    cell_limit: int = DEFAULT_CELL_LIMIT,
) -> MaterializedCube:
    active_scope = scope or {}
    timeout_seconds = max(
        10,
        int(os.environ.get("SORIONO_PRELUDE_PXWEB_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
    )
    retry_attempts = max(
        1,
        int(os.environ.get("SORIONO_PRELUDE_PXWEB_RETRY_ATTEMPTS", DEFAULT_RETRY_ATTEMPTS)),
    )
    timeout = httpx.Timeout(timeout_seconds, connect=min(30, timeout_seconds))
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        metadata_response = _request_with_retry(client, "GET", api_url, attempts=retry_attempts)
        metadata = metadata_response.json()
        variables = metadata.get("variables") or []
        available_codes = [str(variable.get("code") or "") for variable in variables]
        unknown_codes = sorted(set(active_scope) - set(available_codes))
        if unknown_codes:
            raise PxWebUnknownDimensions(unknown_codes, available_codes)
        queries: list[dict[str, Any]] = []
        cell_count = 1
        for variable in variables:
            code = str(variable.get("code") or "")
            available = [str(value) for value in variable.get("values") or []]
            selected = [str(value) for value in active_scope.get(code) or []]
            if selected:
                invalid = sorted(set(selected) - set(available))
                if invalid:
                    raise ValueError(f"Unknown PXWeb values for {code}: {invalid[:10]}")
                selection = {"filter": "item", "values": selected}
                cell_count *= len(selected)
            else:
                selection = {"filter": "all", "values": ["*"]}
                cell_count *= len(available)
            queries.append({"code": code, "selection": selection})
        if cell_count > cell_limit:
            raise PxWebCubeTooLarge(cell_count, cell_limit)

        cache_key = hashlib.sha256(
            json.dumps({"api_url": api_url, "scope": active_scope}, sort_keys=True).encode()
        ).hexdigest()[:20]
        output_dir = state_dir() / "pxweb"
        output_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = output_dir / f"{cache_key}.parquet"
        if parquet_path.exists() and not force:
            return _inspect_parquet(parquet_path, cell_count=cell_count, cached=True, scoped=bool(active_scope))

        response = _request_with_retry(
            client,
            "POST",
            api_url,
            json={"query": queries, "response": {"format": "csv"}},
            attempts=retry_attempts,
        )
    csv_path = output_dir / f"{cache_key}.csv"
    csv_path.write_text(_response_text(response), encoding="utf-8", newline="")
    try:
        _convert_csv_to_parquet(csv_path, parquet_path)
    finally:
        csv_path.unlink(missing_ok=True)
    return _inspect_parquet(parquet_path, cell_count=cell_count, cached=False, scoped=bool(active_scope))


def _convert_csv_to_parquet(csv_path: Path, parquet_path: Path) -> None:
    connection = duckdb.connect(":memory:")
    try:
        csv_literal = _path_literal(csv_path)
        parquet_literal = _path_literal(parquet_path)
        connection.execute(
            f"COPY (SELECT * FROM read_csv_auto({csv_literal}, ignore_errors=true)) "
            f"TO {parquet_literal} (FORMAT PARQUET)"
        )
    finally:
        connection.close()


def _path_literal(path: Path) -> str:
    return "'" + path.resolve().as_posix().replace("'", "''") + "'"


def _response_text(response: httpx.Response) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return response.content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return response.content.decode("utf-8-sig")


def _inspect_parquet(path: Path, *, cell_count: int, cached: bool, scoped: bool) -> MaterializedCube:
    connection = duckdb.connect(":memory:")
    try:
        description = connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
        row_count = int(connection.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(path)]).fetchone()[0])
    finally:
        connection.close()
    return MaterializedCube(
        parquet_path=path,
        row_count=row_count,
        columns=[str(row[0]) for row in description],
        cell_count=cell_count,
        cached=cached,
        scoped=scoped,
    )


def _request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.request(
                method,
                url,
                json=json,
                headers={"User-Agent": "soriono-prelude/0.1"},
            )
            if response.status_code not in RETRY_STATUS:
                response.raise_for_status()
                return response
            last_error = httpx.HTTPStatusError(
                f"PXWeb temporarily unavailable: HTTP {response.status_code}",
                request=response.request,
                response=response,
            )
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2.0**attempt
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            delay = 2.0**attempt
        if attempt < attempts - 1:
            time.sleep(min(delay, 30.0))
    assert last_error is not None
    raise last_error
