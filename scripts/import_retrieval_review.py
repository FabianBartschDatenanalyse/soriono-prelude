from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "tests" / "fixtures" / "retrieval_goldset_150.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    with args.input.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if len(rows) != 150:
        raise SystemExit(f"Review must contain 150 rows, found {len(rows)}")
    reviewed_at = dt.datetime.now(dt.UTC).isoformat()
    cases = []
    for row in rows:
        status = str(row.get("Reviewstatus") or "").strip().casefold()
        if status not in {"freigegeben", "approved"}:
            raise SystemExit(
                f"{row.get('ID')}: Reviewstatus must be Freigegeben/approved"
            )
        expected = [
            item.strip()
            for item in str(row.get("Erwartete Ressourcen") or "").split(";")
            if item.strip()
        ]
        if not expected:
            raise SystemExit(f"{row.get('ID')}: no expected resource")
        cases.append(
            {
                "id": str(row["ID"]).strip(),
                "language": str(row["Sprache"]).strip(),
                "case_type": str(row["Falltyp"]).strip(),
                "question": str(row["Frage"]).strip(),
                "expected": expected,
                "review_status": "approved",
                "reviewer": args.reviewer,
                "reviewed_at": reviewed_at,
                "review_comment": str(row.get("Kommentar") or "").strip(),
            }
        )
    OUTPUT.write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Imported {len(cases)} approved cases into {OUTPUT}")


if __name__ == "__main__":
    main()
