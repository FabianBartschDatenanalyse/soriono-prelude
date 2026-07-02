from __future__ import annotations

import json
from pathlib import Path

from soriono_prelude.catalog import (
    Catalog,
    SearchHit,
    merge_search_hits,
    multilingual_query_values,
    search_query_variants,
)


def test_catalog_contains_full_profile_set() -> None:
    status = Catalog().status()

    assert status["resource_count"] == 22635
    assert status["catalog_version"] == "2026-07-02"


def test_retrieval_goldset_recall_at_20() -> None:
    fixture = Path(__file__).parent / "fixtures" / "retrieval_goldset.json"
    questions = json.loads(fixture.read_text(encoding="utf-8"))
    catalog = Catalog()
    found = 0
    expected_count = 0

    for case in questions:
        hits = catalog.search(case["question"], top_k=20)
        identities = {
            " ".join(
                (
                    str(hit.profile["resource_id"]),
                    str((hit.profile.get("source") or {}).get("landing_page_url") or ""),
                )
            )
            for hit in hits
        }
        expected = set(case["expected"])
        found += sum(any(resource in identity for identity in identities) for resource in expected)
        expected_count += len(expected)

    assert found / expected_count >= 0.90


def test_release_benchmark_has_four_search_languages_per_case() -> None:
    fixtures = Path(__file__).parent / "fixtures"
    cases = json.loads(
        (fixtures / "retrieval_goldset_150.json").read_text(encoding="utf-8")
    )
    search_queries = json.loads(
        (fixtures / "retrieval_search_queries_150.json").read_text(encoding="utf-8")
    )

    assert set(search_queries) == {case["id"] for case in cases}
    for case in cases:
        translations = search_queries[case["id"]]
        assert set(translations) == {"de", "fr", "it", "en"}
        assert translations[case["language"]] == case["question"]
        assert all(value.strip() for value in translations.values())


def test_language_results_are_fused_without_duplicates() -> None:
    profile_a = {"resource_id": "a"}
    profile_b = {"resource_id": "b"}
    profile_c = {"resource_id": "c"}

    hits = merge_search_hits(
        [
            [
                SearchHit(10.0, profile_a, ["population"]),
                SearchHit(9.0, profile_b, ["canton"]),
            ],
            [
                SearchHit(10.0, profile_b, ["bevölkerung"]),
                SearchHit(9.0, profile_c, ["kanton"]),
            ],
        ],
        top_k=20,
    )

    assert [hit.profile["resource_id"] for hit in hits] == ["b", "a", "c"]
    assert hits[0].matched_terms == ["canton", "bevölkerung"]


def test_multilingual_queries_use_fixed_language_order_and_remove_duplicates() -> None:
    queries = multilingual_query_values(
        "population by canton",
        {
            "en": "population by canton",
            "it": "popolazione per cantone",
            "fr": "population par canton",
            "de": "Bevölkerung nach Kanton",
        },
    )

    assert queries == [
        "population by canton",
        "Bevölkerung nach Kanton",
        "population par canton",
        "popolazione per cantone",
    ]


def test_four_language_variants_find_the_same_catalog_resource() -> None:
    hits = search_query_variants(
        Catalog(),
        multilingual_query_values(
            "motorisation rate by canton",
            {
                "de": "Motorisierungsgrad nach Kanton",
                "fr": "Taux de motorisation par canton",
                "it": "Tasso di motorizzazione per cantone",
                "en": "Motorisation rate by canton",
            },
        ),
        top_k=20,
        german_query="Motorisierungsgrad nach Kanton",
    )

    assert any(
        hit.profile["resource_id"]
        == "ckan:motorisierungsgrad-nach-kanton3:45dc455e-03ef-455a-9ac0-29e31b4d9bb3"
        for hit in hits
    )
