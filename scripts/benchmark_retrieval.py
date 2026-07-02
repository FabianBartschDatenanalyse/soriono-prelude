from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from soriono_prelude.catalog import (
    Catalog,
    SearchHit,
    multilingual_query_values,
    search_query_variants,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    fixture = Path(__file__).parents[1] / "tests" / "fixtures" / "retrieval_goldset_150.json"
    questions = json.loads(fixture.read_text(encoding="utf-8"))
    translations_fixture = (
        Path(__file__).parents[1]
        / "tests"
        / "fixtures"
        / "retrieval_search_queries_150.json"
    )
    search_queries = json.loads(translations_fixture.read_text(encoding="utf-8"))
    _validate_fixture(questions, search_queries)
    catalog = Catalog()
    catalog.search("Bevölkerung Schweiz", top_k=20)
    cases = [_run_case(catalog, case, search_queries) for case in questions]
    concurrent_durations = _concurrent_durations(catalog, questions, search_queries)
    expected = sum(item["expected"] for item in cases)
    report = {
        "engine": "sqlite_fts5_parallel_query_variants_rrf",
        "translation_contract": "host_llm_supplies_search_queries",
        "benchmark_supplied_languages": ["de", "fr", "it", "en"],
        "translation_assisted_cases": len(search_queries),
        "questions": len(questions),
        "language_distribution": _counts(questions, "language"),
        "language_metrics": _language_metrics(questions, cases),
        "case_distribution": _counts(questions, "case_type"),
        "recall_at_5": round(sum(item["found_at_5"] for item in cases) / expected, 6),
        "recall_at_20": round(sum(item["found_at_20"] for item in cases) / expected, 6),
        "warm_p95_ms": round(_percentile([item["duration_ms"] for item in cases], 0.95), 2),
        "parallel_8_p95_ms": round(_percentile(concurrent_durations, 0.95), 2),
        "cases": cases,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if args.enforce:
        failures = []
        warnings = []
        if report["recall_at_20"] < 0.95:
            failures.append("Recall@20 < 95%")
        if report["warm_p95_ms"] >= 1500:
            failures.append("warm p95 >= 1.5s")
        if report["recall_at_5"] < 0.85:
            warnings.append("Recall@5 < 85% (accepted non-blocking metric)")
        if report["parallel_8_p95_ms"] >= 5000:
            warnings.append(
                "parallel p95 >= 5s (accepted non-blocking metric)"
            )
        if warnings:
            print("Benchmark warnings: " + "; ".join(warnings), file=sys.stderr)
        if failures:
            raise SystemExit("; ".join(failures))


def _search(
    catalog: Catalog,
    case: dict[str, Any],
    search_queries: dict[str, dict[str, str]],
) -> list[SearchHit]:
    queries = multilingual_query_values(
        case["question"],
        search_queries[case["id"]],
    )
    return search_query_variants(
        catalog,
        queries,
        top_k=20,
        german_query=search_queries[case["id"]]["de"],
    )


def _run_case(
    catalog: Catalog,
    case: dict[str, Any],
    search_queries: dict[str, dict[str, str]],
) -> dict[str, Any]:
    started = time.perf_counter()
    hits = _search(catalog, case, search_queries)
    duration_ms = (time.perf_counter() - started) * 1000
    identities = [str(hit.profile["resource_id"]) for hit in hits]
    expected = [str(item) for item in case["expected"]]
    return {
        "id": case["id"],
        "expected": len(expected),
        "found_at_5": sum(item in identities[:5] for item in expected),
        "found_at_20": sum(item in identities for item in expected),
        "duration_ms": round(duration_ms, 2),
    }


def _concurrent_durations(
    catalog: Catalog,
    questions: list[dict[str, Any]],
    search_queries: dict[str, dict[str, str]],
) -> list[float]:
    def query(case: dict[str, Any]) -> float:
        started = time.perf_counter()
        _search(catalog, case, search_queries)
        return (time.perf_counter() - started) * 1000

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(query, questions))


def _validate_fixture(
    questions: list[dict[str, Any]],
    search_queries: dict[str, dict[str, str]],
) -> None:
    if len(questions) != 150:
        raise RuntimeError("Release benchmark must contain exactly 150 questions")
    expected = {"de": 90, "fr": 25, "it": 15, "en": 20}
    if _counts(questions, "language") != expected:
        raise RuntimeError("Release benchmark has the wrong language distribution")
    if set(search_queries) != {case["id"] for case in questions}:
        raise RuntimeError("Search-query fixture must cover every benchmark case")
    expected_languages = {"de", "fr", "it", "en"}
    for case in questions:
        translations = search_queries[case["id"]]
        if set(translations) != expected_languages:
            raise RuntimeError(f"Incomplete search queries for {case['id']}")
        if translations[case["language"]] != case["question"]:
            raise RuntimeError(f"Original question changed for {case['id']}")
        if any(not value.strip() for value in translations.values()):
            raise RuntimeError(f"Empty search query for {case['id']}")


def _counts(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    return {value: sum(item[key] == value for item in items) for value in dict.fromkeys(item[key] for item in items)}


def _language_metrics(
    questions: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    questions_by_id = {case["id"]: case for case in questions}
    metrics: dict[str, dict[str, float]] = {}
    for language in _counts(questions, "language"):
        language_cases = [
            case
            for case in cases
            if questions_by_id[case["id"]]["language"] == language
        ]
        expected = sum(case["expected"] for case in language_cases)
        metrics[language] = {
            "recall_at_5": round(
                sum(case["found_at_5"] for case in language_cases) / expected,
                6,
            ),
            "recall_at_20": round(
                sum(case["found_at_20"] for case in language_cases) / expected,
                6,
            ),
        }
    return metrics


def _percentile(values: list[float], quantile: float) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[round(quantile * 100) - 1]


if __name__ == "__main__":
    main()
