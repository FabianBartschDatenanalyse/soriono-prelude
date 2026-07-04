from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict


class SearchQueries(TypedDict):
    de: str
    fr: str
    it: str
    en: str


STOPWORDS = {
    "als",
    "am",
    "an",
    "auf",
    "bei",
    "das",
    "den",
    "der",
    "des",
    "die",
    "ein",
    "eine",
    "es",
    "gibt",
    "hat",
    "hoch",
    "im",
    "in",
    "ist",
    "mit",
    "nach",
    "oder",
    "pro",
    "seit",
    "sich",
    "sind",
    "und",
    "von",
    "was",
    "welche",
    "viele",
    "wie",
    "zu",
    "zum",
    "zur",
    "dati",
    "daten",
    "dataset",
    "datensatz",
    "donnees",
    "find",
    "finde",
    "jeu",
    "schweizer",
    "set",
    "suisse",
    "svizzero",
    "swiss",
    "trova",
    "trouve",
}
TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
SEARCH_TRANSLATION = str.maketrans(
    {
        **{char: "a" for char in "äáàâãå"},
        **{char: "e" for char in "ëéèê"},
        **{char: "i" for char in "ïíìî"},
        **{char: "o" for char in "öóòôõ"},
        **{char: "u" for char in "üúùû"},
        "ç": "c",
        "ñ": "n",
        "ß": "ss",
    }
)


def product_root() -> Path:
    configured = os.environ.get("SORIONO_PRELUDE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def catalog_path() -> Path:
    configured = os.environ.get("SORIONO_PRELUDE_CATALOG")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else product_root() / "catalog" / "resources.sqlite"
    )


def state_dir() -> Path:
    configured = os.environ.get("SORIONO_PRELUDE_STATE_DIR")
    path = Path(configured).expanduser() if configured else product_root() / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@dataclass(frozen=True)
class SearchHit:
    score: float
    profile: dict[str, Any]
    matched_terms: list[str]


def merge_search_hits(
    hit_groups: list[list[SearchHit]],
    *,
    top_k: int,
) -> list[SearchHit]:
    """Fuse original-language and German searches with reciprocal-rank fusion."""
    active_limit = max(0, min(int(top_k), 200))
    groups = [group for group in hit_groups if group]
    if not groups:
        return []
    if len(groups) == 1:
        return groups[0][:active_limit]
    fused: dict[str, dict[str, Any]] = {}
    for group in groups:
        for rank, hit in enumerate(group, start=1):
            resource_id = str(hit.profile["resource_id"])
            item = fused.setdefault(
                resource_id,
                {
                    "profile": hit.profile,
                    "score": 0.0,
                    "matched_terms": [],
                    "best_rank": rank,
                },
            )
            item["score"] += 1.0 / (60.0 + rank)
            item["best_rank"] = min(int(item["best_rank"]), rank)
            item["matched_terms"] = list(
                dict.fromkeys([*item["matched_terms"], *hit.matched_terms])
            )
    ranked = sorted(
        fused.values(),
        key=lambda item: (float(item["score"]), -int(item["best_rank"])),
        reverse=True,
    )
    return [
        SearchHit(
            score=round(float(item["score"]) * 1000.0, 6),
            profile=item["profile"],
            matched_terms=item["matched_terms"],
        )
        for item in ranked[:active_limit]
    ]


def multilingual_query_values(
    question: str,
    search_queries: SearchQueries | dict[str, str] | None,
) -> list[str]:
    """Return the original question plus unique DE/FR/IT/EN search formulations."""
    values = [question]
    seen = {normalize_text(question)}
    for language in ("de", "fr", "it", "en"):
        value = str((search_queries or {}).get(language) or "").strip()
        normalized = normalize_text(value)
        if value and normalized not in seen:
            values.append(value)
            seen.add(normalized)
    return values


class Catalog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or catalog_path()).resolve()
        if not self.path.exists():
            raise RuntimeError(
                f"Missing local catalog: {self.path}. Run scripts/build_catalog.py or install a catalog release."
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self.path.as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA mmap_size = 536870912")
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA temp_store = MEMORY")
        return connection

    def status(self) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            metadata = {
                str(row["key"]): json.loads(str(row["value"]))
                for row in connection.execute("SELECT key, value FROM metadata")
            }
            resource_count = int(connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0])
        return {"path": str(self.path), "resource_count": resource_count, **metadata}

    def profile(self, resource_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT profile_json FROM profiles WHERE resource_id = ?",
                (resource_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown resource_id: {resource_id}")
        profile = json.loads(str(row["profile_json"]))
        if not isinstance(profile, dict):
            raise RuntimeError(f"Invalid profile JSON for {resource_id}")
        return profile

    def search(
        self,
        question: str,
        *,
        top_k: int = 20,
        publisher: str | None = None,
        format: str | None = None,
        source_system: str | None = None,
        ready_only: bool = True,
    ) -> list[SearchHit]:
        terms = query_terms(question)
        if not terms:
            return []
        filters: list[str] = []
        filter_parameters: list[Any] = []
        if publisher:
            filters.append("p.publisher = ?")
            filter_parameters.append(publisher)
        if format:
            filters.append("lower(p.format) = lower(?)")
            filter_parameters.append(format)
        if source_system:
            filters.append("p.source_system = ?")
            filter_parameters.append(source_system)
        if ready_only:
            filters.append("p.workflow_smoke_passed = 1")
        active_limit = max(0, min(int(top_k), 200))
        candidate_limit = min(max((active_limit * 3) // 2, 30), 200)
        match_query = " OR ".join(f"{term}*" for term in terms)
        with closing(self.connect()) as connection:
            query = f"""
            SELECT p.rowid AS profile_rowid, profiles_fts.title, profiles_fts.publisher,
                   profiles_fts.search_text, profiles_fts.dimension_text,
                   profiles_fts.measure_text, profiles_fts.sample_value_text,
                   profiles_fts.join_keys,
                   bm25(profiles_fts, 0.0, 8.0, 2.0, 1.0, 2.0, 3.0, 0.5, 2.0) AS rank
            FROM profiles_fts
            JOIN profiles AS p ON p.rowid = profiles_fts.rowid
            WHERE profiles_fts MATCH ?
              {' '.join(f'AND {item}' for item in filters)}
            ORDER BY rank
            LIMIT ?
            """
            rows = connection.execute(
                query,
                [match_query, *filter_parameters, candidate_limit],
            ).fetchall()
            ranked = sorted(
                (_rank_row(row, terms) for row in rows),
                key=lambda item: item[0],
                reverse=True,
            )[:active_limit]
            row_ids = [int(row["profile_rowid"]) for _, row, _ in ranked]
            profiles = {}
            if row_ids:
                placeholders = ",".join("?" for _ in row_ids)
                profiles = {
                    int(row["rowid"]): json.loads(str(row["profile_json"]))
                    for row in connection.execute(
                        f"SELECT rowid, profile_json FROM profiles WHERE rowid IN ({placeholders})",
                        row_ids,
                    )
                }
        return [
            SearchHit(
                score=round(score, 6),
                profile=profiles[int(row["profile_rowid"])],
                matched_terms=_matched_terms(searchable, terms),
            )
            for score, row, searchable in ranked
        ]


def search_query_variants(
    catalog: Catalog,
    queries: list[str],
    *,
    top_k: int,
    german_query: str | None = None,
    publisher: str | None = None,
    format: str | None = None,
    source_system: str | None = None,
    ready_only: bool = True,
) -> list[SearchHit]:
    """Search language variants concurrently and fuse them around a German pivot."""
    active_limit = max(0, min(int(top_k), 200))
    candidate_top_k = min(max(active_limit, 20), 200)

    def search(query: str) -> list[SearchHit]:
        return catalog.search(
            query,
            top_k=candidate_top_k,
            publisher=publisher,
            format=format,
            source_system=source_system,
            ready_only=ready_only,
        )

    if len(queries) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(queries))) as pool:
            groups = list(pool.map(search, queries))
    else:
        groups = [search(queries[0])]
    if len(groups) == 1:
        return groups[0][:active_limit]

    german_index = 0
    normalized_german = normalize_text(german_query)
    if normalized_german:
        german_index = next(
            (
                index
                for index, query in enumerate(queries)
                if normalize_text(query) == normalized_german
            ),
            0,
        )
    primary_indexes = list(dict.fromkeys((0, german_index)))
    secondary_indexes = [
        index for index in range(len(groups)) if index not in primary_indexes
    ]
    primary = merge_search_hits(
        [groups[index] for index in primary_indexes],
        top_k=200,
    )
    if not secondary_indexes:
        return primary[:active_limit]

    original_ids = {
        str(hit.profile["resource_id"]) for hit in groups[0][:20]
    }
    german_ids = {
        str(hit.profile["resource_id"]) for hit in groups[german_index][:20]
    }
    disagreement = german_index != 0 and len(original_ids & german_ids) <= 4
    if not disagreement:
        return primary[:active_limit]

    secondary = merge_search_hits(
        [groups[index] for index in secondary_indexes],
        top_k=200,
    )
    primary_quota = min(10, active_limit)
    combined = [*primary[:primary_quota], *secondary, *primary[primary_quota:]]
    deduplicated = list(
        {str(hit.profile["resource_id"]): hit for hit in combined}.values()
    )
    return deduplicated[:active_limit]


def normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(TOKEN_RE.findall(ascii_text.casefold()))


def query_terms(question: str) -> list[str]:
    terms = []
    for token in normalize_text(question).split():
        if token in STOPWORDS or len(token) < 3:
            continue
        term = _german_search_stem(token)
        if term not in terms:
            terms.append(term)
    return terms[:8]


def _german_search_stem(token: str) -> str:
    for suffix in ("ern", "em", "er", "en", "es", "e", "n", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 5:
            return token[: -len(suffix)]
    return token


def _rank_row(row: sqlite3.Row, terms: list[str]) -> tuple[float, sqlite3.Row, str]:
    title = normalize_text(row["title"])
    searchable = " ".join(
        str(row[field] or "").casefold().translate(SEARCH_TRANSLATION)
        for field in (
            "title",
            "publisher",
            "search_text",
            "dimension_text",
            "measure_text",
            "sample_value_text",
            "join_keys",
        )
    )
    coverage = sum(_term_present(searchable, term) for term in terms)
    title_coverage = sum(_term_present(title, term) for term in terms)
    score = coverage * 1000.0 + title_coverage * 600.0 - float(row["rank"])
    return score, row, searchable


def _matched_terms(searchable: str, terms: list[str]) -> list[str]:
    return [term for term in terms if _term_present(searchable, term)]


def _term_present(searchable: str, term: str) -> bool:
    return any(token.startswith(term) for token in searchable.split())
