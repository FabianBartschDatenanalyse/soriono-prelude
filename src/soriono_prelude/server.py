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
            "Use Soriono Prelude as a complement to web research, not as a reason to suppress it. "
            "When the client has web access, begin with at most two targeted web searches before or in "
            "parallel with the catalog search. If a reliable source directly answers the requested metric, "
            "period, geography, and population, retain that evidence. Use Prelude when the web result is "
            "incomplete or when the question needs structured data, a calculation, a complete ranking, "
            "official open data, or reproducibility. Combine complementary web and catalog evidence, and "
            "never replace a valid web answer with a catalog-miss answer. "
            "Local Swiss open-data tools for the complete profiled catalog. The MCP client must formulate "
            "the question, select resources, inspect schemas, plan joins, write and review SQL, and interpret "
            "results. For comparisons or change-over-time questions, verify that the selected evidence uses "
            "the same measure, geography, population, and at least two suitable periods; continue searching "
            "when the first hit is only a cross-section. "
            "The server performs deterministic retrieval, data access, computation, and formatting only. "
            "Document metadata for PDF, DOC, DOCX, ODT, RTF, and HTML is searched separately from tabular "
            "resources. Document bodies are downloaded, extracted, and cached only when materialized. "
            "Before calling a search tool, create concise German, French, Italian, and English search formulations. "
            "Preserve names, places, years, identifiers, and file formats, and pass the four formulations as "
            "search_queries with keys de, fr, it, and en while keeping the original question unchanged. "
            "For PXWeb profiles, duckdb_readable=false only means that a direct DuckDB reader is not used. "
            "It does not mean that the source is unavailable. Call materialize_resource and use its current "
            "result before making any availability claim; do not reuse an earlier network assumption. "
            "Scientific literature search, statistical tests, regressions, and report generation belong "
            "to Soriono Maestro and are intentionally not part of Prelude. "
            "For every substantive answer based on Prelude, the client MUST call format_reproduction_bundle "
            "as its final Prelude tool call and append the returned markdown under the heading "
            "'Vorgehen und Reproduktion'. Include concise steps, every used source_handle, every used "
            "document_resource_id, the exact SQL when applicable, and the result_handle when available. "
            "This also applies when no SQL was used; never omit the reproduction section."
        ),
    )
    try:
        server._mcp_server.version = __product_version__
    except AttributeError:
        # Private SDK attribute; a future mcp release may rename it. The
        # server must still start even if the version cannot be advertised.
        pass

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
        """Search the catalog using parallel DE/FR/IT/EN formulations.

        Use this for structured data, calculations, complete rankings, official
        open data, reproducibility, or when a bounded web reconnaissance is
        insufficient. A catalog miss does not invalidate reliable web evidence.
        """
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
    def sync_documents(formats: list[str] | None = None) -> dict[str, Any]:
        """Refresh opendata.swiss document metadata in the local index."""
        return active.sync_documents(formats=formats)

    @server.tool()
    def search_documents(
        question: str,
        search_queries: SearchQueries | None = None,
        top_k: int = 20,
        format: str | None = None,
        materialized_only: bool = False,
    ) -> dict[str, Any]:
        """Search document metadata and extracted text with DE/FR/IT/EN formulations."""
        return active.search_documents(
            question,
            search_queries=search_queries,
            top_k=top_k,
            format=format,
            materialized_only=materialized_only,
        )

    @server.tool()
    def get_document_profile(resource_id: str) -> dict[str, Any]:
        """Return metadata, source URL, and extraction status for one document."""
        return active.get_document_profile(resource_id)

    @server.tool()
    def materialize_document(
        resource_id: str,
        force: bool = False,
        ocr: bool = True,
    ) -> dict[str, Any]:
        """Safely download and extract one document, with optional PDF OCR."""
        return active.materialize_document(resource_id, force=force, ocr=ocr)

    @server.tool()
    def read_document(
        resource_id: str,
        query: str | None = None,
        page_number: int | None = None,
        offset: int = 0,
        limit: int = 10,
        max_characters: int = 20_000,
    ) -> dict[str, Any]:
        """Read bounded document chunks, optionally ranked or filtered by PDF page."""
        return active.read_document(
            resource_id,
            query=query,
            page_number=page_number,
            offset=offset,
            limit=limit,
            max_characters=max_characters,
        )

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
        """Download a scoped PXWeb cube into the local Parquet cache.

        PXWeb profiles intentionally have duckdb_readable=false because this
        tool uses the PXWeb API. Call it before claiming that PXWeb is
        unavailable.
        """
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
        sql: str | None = None,
        source_handles: list[str] | None = None,
        steps: list[str] | None = None,
        document_resource_ids: list[str] | None = None,
        result_handle: str | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Format the mandatory final 'Vorgehen und Reproduktion' section."""
        return active.format_reproduction_bundle(
            question=question,
            sql=sql,
            source_handles=source_handles,
            steps=steps,
            document_resource_ids=document_resource_ids,
            result_handle=result_handle,
            rows=rows,
        )

    return server


def run_server() -> None:
    create_server().run(transport="stdio")
