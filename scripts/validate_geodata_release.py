from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    exclusions_path = args.report.with_name("geodata-exclusions-0.3.json")
    exclusions = json.loads(exclusions_path.read_text(encoding="utf-8"))
    excluded_ids = [
        str(resource_id)
        for resource_id in exclusions.get("excluded_resource_ids") or []
    ]
    resources = report.get("resources") or []
    identifiers = [str(item.get("resource_id") or "") for item in resources]
    failures = [item for item in resources if not item.get("passed")]
    failure_ids = {str(item.get("resource_id") or "") for item in failures}
    errors = []
    if report.get("resource_count") != 768 or len(resources) != 768:
        errors.append(
            f"expected 768 resources, report contains {len(resources)}"
        )
    if len(set(identifiers)) != 768:
        errors.append("resource IDs are missing or duplicated")
    if len(excluded_ids) != 22 or len(set(excluded_ids)) != 22:
        errors.append("expected exactly 22 unique geodata exclusions")
    if failure_ids != set(excluded_ids):
        errors.append("audit failures do not match the approved exclusion list")
    if set(excluded_ids) - set(identifiers):
        errors.append("approved exclusions are missing from the audit")
    _validate_catalog_readiness(excluded_ids, errors)
    if errors:
        failure_preview = "\n".join(
            f"- {item.get('resource_id')}: {item.get('reason')}"
            for item in failures[:30]
        )
        raise SystemExit(
            "Geodata release gate failed: "
            + "; ".join(errors)
            + (f"\n{failure_preview}" if failure_preview else "")
        )
    print(
        "Geodata release gate passed: "
        f"{len(resources) - len(excluded_ids)} supported profiles passed; "
        f"{len(excluded_ids)} unavailable profiles are excluded from ready_only."
    )


def _validate_catalog_readiness(
    excluded_ids: list[str],
    errors: list[str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    placeholders = ",".join("?" for _ in excluded_ids)
    catalog = root / "catalog" / "resources.sqlite"
    with sqlite3.connect(catalog) as connection:
        rows = connection.execute(
            "SELECT resource_id, workflow_smoke_passed "
            f"FROM profiles WHERE resource_id IN ({placeholders})",
            excluded_ids,
        ).fetchall()
    if len(rows) != len(excluded_ids):
        errors.append("excluded profiles are missing from the catalog")
        return
    ready_ids = [resource_id for resource_id, ready in rows if bool(ready)]
    if ready_ids:
        errors.append(
            f"{len(ready_ids)} excluded profiles remain ready"
        )


if __name__ == "__main__":
    main()
