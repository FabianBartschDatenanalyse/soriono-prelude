from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from deep_translator import GoogleTranslator

LANGUAGES = ("de", "fr", "it", "en")
PROTECTED_TOKEN = re.compile(
    r"https?://\S+|"
    r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|"
    r"\b(?:CSV|JSON|SQL|GeoJSON|GeoParquet|Parquet|PXWeb|QIP21|SJYID|LoRa)\b",
    re.IGNORECASE,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1]
        / "tests"
        / "fixtures"
        / "retrieval_search_queries_150.json",
    )
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    fixture = (
        Path(__file__).parents[1]
        / "tests"
        / "fixtures"
        / "retrieval_goldset_150.json"
    )
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    generated = _load_checkpoint(args.output)
    curated_german_fixture = (
        Path(__file__).parents[1]
        / "tests"
        / "fixtures"
        / "retrieval_curated_german_queries_60.json"
    )
    curated_german = {
        case_id[:4]: query
        for case_id, query in json.loads(
            curated_german_fixture.read_text(encoding="utf-8")
        ).items()
    }
    for case in cases:
        curated_query = curated_german.get(str(case["id"])[:4])
        if curated_query:
            generated.setdefault(str(case["id"]), {})["de"] = curated_query

    pending = [
        case
        for case in cases
        if set(generated.get(str(case["id"]), {})) != set(LANGUAGES)
    ]
    existing = {
        str(case["id"]): dict(generated.get(str(case["id"]), {}))
        for case in pending
    }

    def generate(case: dict[str, Any]) -> tuple[str, dict[str, str]]:
        case_id = str(case["id"])
        return case_id, _translate_case(case, existing[case_id])

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 12))) as pool:
        for index, (case_id, translations) in enumerate(
            pool.map(generate, pending),
            start=len(cases) - len(pending) + 1,
        ):
            generated[case_id] = translations
            _write(args.output, generated)
            print(f"{index:03d}/{len(cases)} {case_id}", flush=True)

    _validate(cases, generated)
    _write(args.output, generated)


def _translate_case(
    case: dict[str, Any],
    existing: dict[str, str],
) -> dict[str, str]:
    source_language = str(case["language"])
    question = str(case["question"])
    translations = dict(existing)
    translations[source_language] = question
    for target_language in LANGUAGES:
        if target_language in translations:
            continue
        translations[target_language] = _translate_with_retry(
            question,
            source_language=source_language,
            target_language=target_language,
        )
    return translations


def _translate_with_retry(
    text: str,
    *,
    source_language: str,
    target_language: str,
) -> str:
    protected_text, protected = _protect(text)
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            translated = GoogleTranslator(
                source=source_language,
                target=target_language,
            ).translate(protected_text)
            restored = _restore(str(translated), protected)
            if not restored.strip():
                raise RuntimeError("translation returned an empty string")
            return restored
        except Exception as exc:  # pragma: no cover - external retry path
            last_error = exc
            time.sleep(min(2**attempt, 16))
    raise RuntimeError(
        f"Translation {source_language}->{target_language} failed"
    ) from last_error


def _protect(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        marker = f"KEEPXTOKENX{len(protected)}"
        protected[marker] = match.group(0)
        return marker

    return PROTECTED_TOKEN.sub(replace, text), protected


def _restore(text: str, protected: dict[str, str]) -> str:
    restored = text
    for marker, value in protected.items():
        restored = re.sub(re.escape(marker), value, restored, flags=re.IGNORECASE)
    return restored


def _load_checkpoint(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RuntimeError("Translation checkpoint must be a JSON object")
    return loaded


def _validate(
    cases: list[dict[str, Any]],
    generated: dict[str, dict[str, str]],
) -> None:
    expected_ids = {str(case["id"]) for case in cases}
    if set(generated) != expected_ids:
        raise RuntimeError("Translation fixture must cover all benchmark cases")
    for case in cases:
        translations = generated[str(case["id"])]
        if set(translations) != set(LANGUAGES):
            raise RuntimeError(f"Incomplete translations for {case['id']}")
        if translations[str(case["language"])] != str(case["question"]):
            raise RuntimeError(f"Original question changed for {case['id']}")
        if any(not value.strip() for value in translations.values()):
            raise RuntimeError(f"Empty translation for {case['id']}")


def _write(path: Path, generated: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(generated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
