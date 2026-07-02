from __future__ import annotations

import datetime as dt
import decimal
from pathlib import Path
from typing import Any

import duckdb

from soriono_prelude.catalog import (
    Catalog,
    multilingual_query_values,
    search_query_variants,
)
from soriono_prelude.duckdb_runtime import (
    SpatialExtensionUnavailable,
    SpatialSourceUnavailable,
    open_connection,
)
from soriono_prelude.pxweb import (
    PxWebCubeTooLarge,
    PxWebUnknownDimensions,
    materialize_pxweb,
)
from soriono_prelude.reproduction import reproduction_bundle
from soriono_prelude.results import ResultStore
from soriono_prelude.sources import SourceRegistry, source_payload
from soriono_prelude.sql import validate_sql as check_sql


class SorionoPreludeTools:
    def __init__(
        self,
        *,
        catalog: Catalog | None = None,
        registry: SourceRegistry | None = None,
        results: ResultStore | None = None,
    ) -> None:
        self.catalog = catalog or Catalog()
        self.registry = registry or SourceRegistry()
        self.results = results or ResultStore()

    def catalog_status(self) -> dict[str, Any]:
        return self.catalog.status()

    def search_resources(
        self,
        question: str,
        *,
        search_queries: dict[str, str] | None = None,
        top_k: int = 20,
        max_resources: int = 10,
        publisher: str | None = None,
        format: str | None = None,
        source_system: str | None = None,
        ready_only: bool = True,
    ) -> dict[str, Any]:
        queries = multilingual_query_values(question, search_queries)
        hits = search_query_variants(
            self.catalog,
            queries,
            top_k=top_k,
            german_query=(search_queries or {}).get("de"),
            publisher=publisher,
            format=format,
            source_system=source_system,
            ready_only=ready_only,
        )
        resources = []
        for hit in hits[: max(0, int(max_resources))]:
            record = self.registry.register_profile(hit.profile)
            resources.append(
                {
                    **_planning_profile(hit.profile),
                    "score": hit.score,
                    "matched_terms": hit.matched_terms,
                    "source": source_payload(record) if record else _public_source(hit.profile),
                }
            )
        return {
            "question": question,
            "search_queries": search_queries,
            "search_engine": (
                "sqlite_fts5_parallel_multilingual_rrf"
                if len(queries) > 1
                else "sqlite_fts5"
            ),
            "catalog_size": self.catalog.status()["resource_count"],
            "returned_count": len(resources),
            "resources": resources,
        }

    def get_resource_profile(self, resource_id: str) -> dict[str, Any]:
        profile = self.catalog.profile(resource_id)
        record = self.registry.register_profile(profile)
        return {
            "resource": profile,
            "source": source_payload(record) if record else _public_source(profile),
        }

    def get_context_bundle(
        self,
        question: str,
        *,
        search_queries: dict[str, str] | None = None,
        resource_ids: list[str] | None = None,
        top_k: int = 20,
        max_resources: int = 10,
    ) -> dict[str, Any]:
        search = None
        selected_ids = resource_ids
        if not selected_ids:
            search = self.search_resources(
                question,
                search_queries=search_queries,
                top_k=top_k,
                max_resources=max_resources,
            )
            selected_ids = [str(item["resource_id"]) for item in search["resources"]]
        profiles = [self.catalog.profile(resource_id) for resource_id in selected_ids]
        sources = []
        for profile in profiles:
            record = self.registry.register_profile(profile)
            if record:
                sources.append(source_payload(record))
        return {
            "question": question,
            "context_type": "soriono_prelude_local_profiles",
            "resources": [_planning_profile(profile, include_values=True) for profile in profiles],
            "sources": sources,
            "search": search,
            "client_responsibilities": [
                "Define the question and required resource roles.",
                "Inspect source schemas and codes before writing SQL.",
                "Check grain and key coding before every join.",
                "Write and review SQL and interpretation.",
                "Report limitations, sources, and reproducibility details.",
            ],
        }

    def materialize_resource(
        self,
        resource_id: str,
        *,
        scope: dict[str, list[str]] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        profile = self.catalog.profile(resource_id)
        source = profile.get("source") or {}
        if str(source.get("access_method")) != "pxweb_api":
            return {
                "status": "not_pxweb_api",
                "resource_id": resource_id,
                "message": "This resource is registered directly and does not need PXWeb materialization.",
            }
        api_url = str(source.get("api_url") or source.get("source_url") or "")
        try:
            cube = materialize_pxweb(api_url, scope=scope, force=force)
        except PxWebUnknownDimensions as exc:
            return {
                "status": "invalid_scope",
                "resource_id": resource_id,
                "unknown_dimensions": exc.unknown,
                "available_dimensions": exc.available,
                "suggestions": exc.suggestions,
                "message": str(exc),
            }
        except PxWebCubeTooLarge as exc:
            return {
                "status": "too_large",
                "resource_id": resource_id,
                "cell_count": exc.cell_count,
                "cell_limit": exc.cell_limit,
                "message": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "failed",
                "resource_id": resource_id,
                "error": str(exc)[:2000],
                "exception_type": exc.__class__.__name__,
            }
        record = self.registry.register_materialized(
            profile,
            parquet_path=cube.parquet_path,
            columns=cube.columns,
            metadata={"cell_count": cube.cell_count, "scoped": cube.scoped},
        )
        return {
            "status": "materialized",
            "resource_id": resource_id,
            "source": source_payload(record),
            "row_count": cube.row_count,
            "columns": cube.columns,
            "cell_count": cube.cell_count,
            "cached": cube.cached,
            "scoped": cube.scoped,
        }

    def inspect_source(
        self,
        source_handle: str,
        *,
        sample_rows: int = 5,
        distinct_columns: list[str] | None = None,
        distinct_limit: int = 20,
    ) -> dict[str, Any]:
        record = self.registry.get(source_handle)
        active_sample_rows = max(0, min(int(sample_rows), 100))
        active_distinct_limit = max(1, min(int(distinct_limit), 200))
        try:
            connection = open_connection([record])
        except SpatialExtensionUnavailable as exc:
            return {
                "status": "spatial_extension_unavailable",
                "source": source_payload(record),
                "error": str(exc),
            }
        except SpatialSourceUnavailable as exc:
            return {
                "status": "spatial_source_unavailable",
                "source": source_payload(record),
                "error": str(exc),
            }
        try:
            sql_name = _identifier(str(record.sql_name))
            schema_rows = connection.execute(f"DESCRIBE SELECT * FROM {sql_name}").fetchall()
            sample_result = connection.execute(
                f"SELECT * FROM {sql_name} LIMIT {active_sample_rows}"
            )
            sample = _rows(sample_result)
            distinct: dict[str, list[Any]] = {}
            available = {str(row[0]) for row in schema_rows}
            for column in distinct_columns or []:
                if column not in available:
                    raise ValueError(f"Unknown column: {column}")
                quoted = '"' + column.replace('"', '""') + '"'
                values = connection.execute(
                    f"SELECT DISTINCT {quoted} FROM {sql_name} "
                    f"WHERE {quoted} IS NOT NULL ORDER BY 1 LIMIT {active_distinct_limit}"
                ).fetchall()
                distinct[column] = [_json_safe(row[0]) for row in values]
        finally:
            connection.close()
        return {
            "source": source_payload(record),
            "schema": [
                {"column_name": row[0], "column_type": row[1], "null": row[2]}
                for row in schema_rows
            ],
            "sample_rows": sample,
            "distinct_values": distinct,
        }

    def validate_sql(self, sql: str, *, source_handles: list[str]) -> dict[str, Any]:
        return check_sql(sql, [self.registry.get(handle) for handle in source_handles])

    def execute_sql(
        self,
        sql: str,
        *,
        source_handles: list[str],
        limit: int | None = None,
    ) -> dict[str, Any]:
        return self.results.execute(
            sql,
            [self.registry.get(handle) for handle in source_handles],
            limit=limit,
        )

    def get_result_page(
        self,
        result_handle: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self.results.page(result_handle, offset=offset, limit=limit)

    def get_result_summary(self, result_handle: str) -> dict[str, Any]:
        return self.results.summary(result_handle)

    def format_reproduction_bundle(
        self,
        *,
        question: str,
        sql: str,
        source_handles: list[str],
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return reproduction_bundle(
            question=question,
            sql=sql,
            sources=[self.registry.get(handle) for handle in source_handles],
            rows=rows,
        )


def _planning_profile(profile: dict[str, Any], *, include_values: bool = False) -> dict[str, Any]:
    keys = (
        "resource_id",
        "title",
        "publisher",
        "source_system",
        "content_type",
        "analytical_suitability",
        "dimensions",
        "measures",
        "columns",
        "geo_levels",
        "years",
        "units",
        "join_keys",
        "semantic_warnings",
        "readiness",
    )
    result = {key: profile.get(key) for key in keys}
    result["source"] = _public_source(profile)
    if include_values:
        result["value_summaries"] = profile.get("value_summaries") or {}
    return result


def _public_source(profile: dict[str, Any]) -> dict[str, Any]:
    source = profile.get("source") or {}
    return {
        "resource_id": profile.get("resource_id"),
        "source_url": source.get("source_url"),
        "api_url": source.get("api_url"),
        "landing_page_url": source.get("landing_page_url"),
        "format": source.get("format"),
        "access_method": source.get("access_method"),
        "materialization_required": source.get("access_method") == "pxweb_api",
    }


def _rows(result: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [item[0] for item in result.description or []]
    return [
        {column: _json_safe(value) for column, value in zip(columns, row, strict=False)}
        for row in result.fetchall()
    ]


def _json_safe(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime, dt.time, decimal.Decimal, Path)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
