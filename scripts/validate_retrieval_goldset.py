from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "retrieval_goldset_150.json"
EXPECTED_LANGUAGES = {"de": 90, "fr": 25, "it": 15, "en": 20}


def main() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    errors: list[str] = []
    if len(cases) != 150:
        errors.append(f"expected 150 cases, found {len(cases)}")
    languages = Counter(str(case.get("language") or "") for case in cases)
    if dict(languages) != EXPECTED_LANGUAGES:
        errors.append(f"language distribution is {dict(languages)}")
    for case in cases:
        case_id = str(case.get("id") or "<missing>")
        if case.get("review_status") != "approved":
            errors.append(f"{case_id}: review_status is not approved")
        if not str(case.get("reviewer") or "").strip():
            errors.append(f"{case_id}: reviewer is missing")
        if not str(case.get("reviewed_at") or "").strip():
            errors.append(f"{case_id}: reviewed_at is missing")
        if not case.get("expected"):
            errors.append(f"{case_id}: expected resources are missing")
    if errors:
        preview = "\n".join(errors[:30])
        remaining = len(errors) - min(len(errors), 30)
        suffix = f"\n... and {remaining} more" if remaining else ""
        raise SystemExit(f"Retrieval goldset review gate failed:\n{preview}{suffix}")
    print("Retrieval goldset review gate passed: 150/150 approved.")


if __name__ == "__main__":
    main()
