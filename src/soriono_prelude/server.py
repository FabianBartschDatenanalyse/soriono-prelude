from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from soriono_prelude import __product_version__
from soriono_prelude.catalog import SearchQueries
from soriono_prelude.tools import SorionoPreludeTools


def create_server(tools: SorionoPreludeTools | None = None) -> FastMCP:
    active = tools or SorionoPreludeTools()
    server = FastMCP(
        "soriono-prelude",
        instructions=(
            "Local Swiss open-data tools for the complete profiled catalog. The MCP client must formulate "
            "the question, select resources, inspect schemas, plan joins, write and review SQL, and "
            "interpret results. "
            "The server performs deterministic retrieval, data access, computation, and formatting only. "
            "Before calling a search tool, create concise German, French, Italian, and English search formulations. "
            "Preserve names, places, years, identifiers, and file formats, and pass the four formulations as "
            "search_queries with keys de, fr, it, and en while keeping the original question unchanged. "
            "Scientific literature search, statistical tests, regressions, and report generation belong "
            "to Soriono Maestro and are intentionally not part of Prelude."
        ),
    )
    server._mcp_server.version = __product_version__

    @server.tool()
    def catalog_status() -> dict[str, Any]:
        """Return local catalog version, path, and resource count."""
        return active.catalog_status()

    @server.tool()
    def search_resources(
        question: str,
        search_queries: SearchQueries | None = None,
        top_k: int = 20,
        max_resources: int = 10,
        publisher: str | None = None,
        format: str | None = None,
        source_system: str | None = None,
        ready_only: bool = True,
    ) -> dict[str, Any]:
        """Search locally using parallel DE/FR/IT/EN formulations supplied in search_queries."""
        return active.search_resources(
            question,
            search_queries=search_queries,
            top_k=top_k,
            max_resources=max_resources,
            publisher=publisher,
            format=format,
            source_system=source_system,
            ready_only=ready_only,
        )

    @server.tool()
    def get_resource_profile(resource_id: str) -> dict[str, Any]:
        """Return one complete local planning profile."""
        return active.get_resource_profile(resource_id)

    @server.tool()
    def get_context_bundle(
        question: str,
        search_queries: SearchQueries | None = None,
        resource_ids: list[str] | None = None,
        top_k: int = 20,
        max_resources: int = 10,
    ) -> dict[str, Any]:
        """Return planning context.

        Supply DE/FR/IT/EN search_queries when resources are selected automatically.
        """
        return active.get_context_bundle(
            question,
            search_queries=search_queries,
            resource_ids=resource_ids,
            top_k=top_k,
            max_resources=max_resources,
        )

    @server.tool()
    def materialize_resource(
        resource_id: str,
        scope: dict[str, list[str]] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Download a scoped PXWeb cube into the local Parquet cache."""
        return active.materialize_resource(resource_id, scope=scope, force=force)

    @server.tool()
    def inspect_source(
        source_handle: str,
        sample_rows: int = 5,
        distinct_columns: list[str] | None = None,
        distinct_limit: int = 20,
    ) -> dict[str, Any]:
        """Inspect a live source schema, sample rows, and selected values."""
        return active.inspect_source(
            source_handle,
            sample_rows=sample_rows,
            distinct_columns=distinct_columns,
            distinct_limit=distinct_limit,
        )

    @server.tool()
    def validate_sql(sql: str, source_handles: list[str]) -> dict[str, Any]:
        """Validate one read-only DuckDB query against explicit sources."""
        return active.validate_sql(sql, source_handles=source_handles)

    @server.tool()
    def execute_sql(
        sql: str,
        source_handles: list[str],
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Execute one validated read-only DuckDB query."""
        return active.execute_sql(sql, source_handles=source_handles, limit=limit)

    @server.tool()
    def get_result_page(
        result_handle: str,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Return one bounded page from a complete locally stored SQL result."""
        return active.get_result_page(result_handle, offset=offset, limit=limit)

    @server.tool()
    def get_result_summary(result_handle: str) -> dict[str, Any]:
        """Return metadata for a complete locally stored SQL result."""
        return active.get_result_summary(result_handle)

    @server.tool()
    def format_reproduction_bundle(
        question: str,
        sql: str,
        source_handles: list[str],
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Format exact sources and SQL for reproduction."""
        return active.format_reproduction_bundle(
            question=question,
            sql=sql,
            source_handles=source_handles,
            rows=rows,
        )

    return server


def run_server() -> None:
    create_server().run(transport="stdio")
