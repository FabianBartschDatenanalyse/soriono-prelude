from __future__ import annotations

import hashlib
import os
from pathlib import Path

import duckdb
import httpx

from soriono_prelude import USER_AGENT
from soriono_prelude.catalog import state_dir
from soriono_prelude.geodata_resolvers import (
    GeodataResolverUnavailable,
    materialize_resolved_source,
)
from soriono_prelude.sources import SourceRecord, sql_literal

GEO_FORMATS = {"gpkg", "shp", "kml"}
DEFAULT_MAX_GEODATA_BYTES = 500_000_000


class SpatialExtensionUnavailable(RuntimeError):
    pass


class SpatialSourceUnavailable(RuntimeError):
    pass


def open_connection(sources: list[SourceRecord] | None = None) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    try:
        for source in sources or []:
            reader = source.duckdb_reader
            if _requires_spatial(source):
                load_spatial(connection)
                reader = _spatial_reader(source, connection)
            connection.execute(
                f"CREATE TEMP VIEW {_identifier(str(source.sql_name))} AS "
                f"SELECT * FROM {reader}"
            )
    except Exception:
        connection.close()
        raise
    return connection


def csv_reject_count(connection: duckdb.DuckDBPyConnection) -> int:
    """Count distinct rejected CSV lines recorded by store_rejects readers.

    The reject tables only exist after a CSV scan ran with store_rejects; the
    same file may be scanned more than once per connection (DESCRIBE + COPY),
    so rejected lines are deduplicated per file.
    """
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT DISTINCT s.file_path, e.line "
            "FROM reject_errors AS e "
            "JOIN reject_scans AS s ON e.scan_id = s.scan_id AND e.file_id = s.file_id"
            ")"
        ).fetchone()
    except duckdb.Error:
        return 0
    return int(row[0]) if row else 0


def load_spatial(connection: duckdb.DuckDBPyConnection) -> None:
    try:
        connection.execute("LOAD spatial")
        return
    except duckdb.Error:
        pass
    try:
        connection.execute("INSTALL spatial")
        connection.execute("LOAD spatial")
    except duckdb.Error as exc:
        raise SpatialExtensionUnavailable(
            "DuckDB Spatial could not be installed or loaded. "
            "The first geodata access requires internet; later requests use the local extension cache."
        ) from exc


def _requires_spatial(source: SourceRecord) -> bool:
    return str(source.format or "").casefold() in GEO_FORMATS or source.duckdb_reader.lstrip().casefold().startswith(
        "st_read("
    )


def _spatial_reader(
    source: SourceRecord,
    connection: duckdb.DuckDBPyConnection,
) -> str:
    try:
        resolved = materialize_resolved_source(source, connection)
    except GeodataResolverUnavailable as exc:
        raise SpatialSourceUnavailable(str(exc)) from exc
    if resolved is not None:
        return f"read_parquet({sql_literal(resolved.as_posix())})"
    if not source.source_url.startswith(("https://", "http://")):
        return source.duckdb_reader
    local_path = _download_spatial_source(source)
    path = local_path.as_posix()
    if str(source.format or "").casefold() == "shp" and local_path.suffix.casefold() == ".zip":
        path = f"/vsizip/{path}"
    return f"ST_Read({sql_literal(path)})"


def _download_spatial_source(source: SourceRecord) -> Path:
    source_url = source.source_url
    digest = hashlib.sha256(source_url.encode()).hexdigest()[:24]
    suffix = Path(source_url.split("?", 1)[0]).suffix.casefold()
    if not suffix:
        suffix = {
            "gpkg": ".gpkg",
            "kml": ".kml",
            "shp": ".zip",
        }.get(str(source.format or "").casefold(), ".geo")
    output_dir = state_dir() / "geodata"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{digest}{suffix}"
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    maximum_bytes = max(
        1,
        int(os.environ.get("SORIONO_PRELUDE_MAX_GEODATA_BYTES", DEFAULT_MAX_GEODATA_BYTES)),
    )
    downloaded = 0
    try:
        with httpx.stream(
            "GET",
            source_url,
            timeout=httpx.Timeout(120, connect=30),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as response:
            response.raise_for_status()
            content_length = int(response.headers.get("content-length") or 0)
            if content_length > maximum_bytes:
                raise SpatialSourceUnavailable(
                    f"Geodata source exceeds the configured download limit: {content_length} bytes"
                )
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    downloaded += len(chunk)
                    if downloaded > maximum_bytes:
                        raise SpatialSourceUnavailable(
                            f"Geodata source exceeds the configured download limit: {maximum_bytes} bytes"
                        )
                    output.write(chunk)
        os.replace(temporary, destination)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, SpatialSourceUnavailable):
            raise
        raise SpatialSourceUnavailable(f"Could not download geodata source: {source_url}") from exc
    return destination


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
