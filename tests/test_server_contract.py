from __future__ import annotations

import asyncio
from pathlib import Path

from soriono_prelude.server import create_server


def test_mcp_exposes_expected_tools_without_answer_question() -> None:
    server = create_server()
    tools = asyncio.run(server.list_tools())
    names = {tool.name for tool in tools}

    assert names == {
        "catalog_status",
        "search_resources",
        "get_resource_profile",
        "get_context_bundle",
        "materialize_resource",
        "inspect_source",
        "validate_sql",
        "execute_sql",
        "get_result_page",
        "get_result_summary",
        "format_reproduction_bundle",
    }
    assert "answer_question" not in names
    assert "search_literature" not in names
    assert "run_statistical_test" not in names
    assert "run_regression" not in names
    assert "create_scientific_report" not in names


def test_server_reports_product_version() -> None:
    assert create_server()._mcp_server.version == "0.3.0-rc.1"


def test_search_tools_publish_multilingual_query_parameter() -> None:
    tools = asyncio.run(create_server().list_tools())

    for name in ("search_resources", "get_context_bundle"):
        tool = next(item for item in tools if item.name == name)
        assert "search_queries" in tool.inputSchema["properties"]
        assert "DE/FR/IT/EN" in (tool.description or "")
        assert set(tool.inputSchema["$defs"]["SearchQueries"]["required"]) == {
            "de",
            "fr",
            "it",
            "en",
        }


def test_runtime_has_no_internal_llm_adapter() -> None:
    source_root = Path(__file__).parents[1] / "src" / "soriono_prelude"
    runtime = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py"))

    assert "LLMAdapter" not in runtime
    assert "openai" not in runtime.casefold()
    assert "anthropic" not in runtime.casefold()
