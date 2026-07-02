from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
LANGUAGES = ["de"] * 90 + ["fr"] * 25 + ["it"] * 15 + ["en"] * 20
PREFIXES = {
    "de": "Finde den Schweizer Datensatz",
    "fr": "Trouve le jeu de données suisse",
    "it": "Trova il set di dati svizzero",
    "en": "Find the Swiss dataset",
}


def main() -> None:
    database = ROOT / "catalog" / "resources.sqlite"
    output = ROOT / "tests" / "fixtures" / "retrieval_candidates_150.json"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT resource_id, title FROM profiles "
            "WHERE workflow_smoke_passed = 1 AND length(title) BETWEEN 18 AND 120 "
            "ORDER BY resource_id"
        ).fetchall()
    title_counts = Counter(str(title) for _, title in rows)
    candidates = [
        (str(resource_id), str(title))
        for resource_id, title in rows
        if title_counts[str(title)] <= 5
    ]
    short = [item for item in candidates if 2 <= len(item[1].split()) <= 5]
    short_stride = max(1, len(short) // 30)
    multi = short[::short_stride][:30]
    multi_ids = {item[0] for item in multi}
    remaining = [item for item in candidates if item[0] not in multi_ids]
    stride = max(1, len(remaining) // 135)
    selected = multi[:15] + remaining[::stride][:135]
    partners = multi[15:30]
    if len(selected) < 150 or len(partners) < 15:
        raise RuntimeError("Catalog does not provide enough benchmark candidates")

    cases = []
    for index, ((resource_id, title), language) in enumerate(
        zip(selected[:150], LANGUAGES, strict=True)
    ):
        case_type = "direct"
        query_title = title
        expected = [resource_id]
        if index < 15:
            case_type = "multi_source"
            partner_id, partner_title = partners[index]
            query_title = f"{title}; {partner_title}"
            expected.append(partner_id)
        elif index >= 120:
            case_type = "synonym_typo"
            query_title = _single_typo(title)
        cases.append(
            {
                "id": f"q{index + 1:03d}_{language}_{case_type}",
                "language": language,
                "case_type": case_type,
                "question": f"{PREFIXES[language]}: {query_title}",
                "expected": expected,
            }
        )

    distribution = defaultdict(int)
    for case in cases:
        distribution[case["language"]] += 1
    if dict(distribution) != {"de": 90, "fr": 25, "it": 15, "en": 20}:
        raise RuntimeError(f"Unexpected language distribution: {dict(distribution)}")
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} cases to {output}")


def _single_typo(title: str) -> str:
    words = title.split()
    for index, word in enumerate(words):
        if len(word) >= 8:
            middle = len(word) // 2
            words[index] = word[:middle] + word[middle + 1 :]
            break
    return " ".join(words)


if __name__ == "__main__":
    main()
