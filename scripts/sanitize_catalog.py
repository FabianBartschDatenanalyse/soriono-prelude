from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import shutil
import sqlite3
from pathlib import Path
from typing import Any

GEO_FORMATS = {"GPKG", "SHP", "KML"}
NONFINITE_WARNING = "Non-finite numeric profile values were normalized to null."


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--input", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--manifest", type=Path, required=True)
    result.add_argument("--catalog-version", required=True)
    result.add_argument("--geodata-report", type=Path)
    return result


def main() -> None:
    args = parser().parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    manifest_path = args.manifest.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    geodata = _load_geodata_report(args.geodata_report)
    shutil.copyfile(source, output)
    stats = _sanitize(output, catalog_version=args.catalog_version, geodata=geodata)
    digest = _sha256(output)
    manifest = {
        "catalog_version": args.catalog_version,
        "created_at": stats["created_at"],
        "source": "opendata.swiss and BFS PXWeb profiles exported from Vespa",
        "resource_count": stats["resource_count"],
        "counts": stats["counts"],
        "readiness_snapshot": stats["created_at"],
        "path": "catalog/resources.sqlite",
        "size_bytes": output.stat().st_size,
        "sha256": digest,
        "sanitized_nonfinite_profiles": stats["sanitized_nonfinite_profiles"],
        "geodata_profiles_audited": stats["geodata_profiles_audited"],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def _sanitize(
    path: Path,
    *,
    catalog_version: str,
    geodata: dict[str, bool],
) -> dict[str, Any]:
    created_at = dt.datetime.now(dt.UTC).isoformat()
    sanitized_nonfinite = 0
    geodata_audited = 0
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT resource_id, format, profile_json FROM profiles"
        ).fetchall()
        for resource_id, format_value, raw in rows:
            profile = json.loads(str(raw))
            cleaned, changed = _finite(profile)
            if changed:
                sanitized_nonfinite += 1
                warnings = [str(item) for item in cleaned.get("semantic_warnings") or []]
                if NONFINITE_WARNING not in warnings:
                    warnings.append(NONFINITE_WARNING)
                cleaned["semantic_warnings"] = warnings
            workflow = None
            if str(format_value or "").upper() in GEO_FORMATS and resource_id in geodata:
                workflow = bool(geodata[resource_id])
                readiness = dict(cleaned.get("readiness") or {})
                readiness["workflow_smoke_passed"] = workflow
                cleaned["readiness"] = readiness
                geodata_audited += 1
            encoded = json.dumps(
                cleaned,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            if changed or workflow is not None or encoded != raw:
                connection.execute(
                    "UPDATE profiles SET profile_json = ?, workflow_smoke_passed = COALESCE(?, workflow_smoke_passed) "
                    "WHERE resource_id = ?",
                    (encoded, int(workflow) if workflow is not None else None, resource_id),
                )
        counts = {
            key: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM profiles "
                    f"WHERE json_extract(profile_json, '$.readiness.{key}') = 1"
                ).fetchone()[0]
            )
            for key in (
                "retrievable",
                "hydrated",
                "duckdb_readable",
                "materializable",
                "join_keys_detected",
                "workflow_smoke_passed",
            )
        }
        resource_count = int(connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0])
        metadata = {
            "catalog_version": catalog_version,
            "created_at": created_at,
            "resource_count": resource_count,
            "counts": counts,
            "readiness_snapshot": created_at,
        }
        for key, value in metadata.items():
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False, allow_nan=False)),
            )
        connection.commit()
        _validate(connection, resource_count)
    finally:
        connection.close()
    return {
        "created_at": created_at,
        "resource_count": resource_count,
        "counts": counts,
        "sanitized_nonfinite_profiles": sanitized_nonfinite,
        "geodata_profiles_audited": geodata_audited,
    }


def _validate(connection: sqlite3.Connection, expected_count: int) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity check failed")
    checks = {
        "profile count": connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0],
        "FTS count": connection.execute("SELECT COUNT(*) FROM profiles_fts").fetchone()[0],
        "invalid JSON": connection.execute(
            "SELECT COUNT(*) FROM profiles WHERE NOT json_valid(profile_json)"
        ).fetchone()[0],
        "duplicate ids": connection.execute(
            "SELECT COUNT(*) FROM (SELECT resource_id FROM profiles GROUP BY resource_id HAVING COUNT(*) > 1)"
        ).fetchone()[0],
    }
    if checks["profile count"] != expected_count or checks["FTS count"] != expected_count:
        raise RuntimeError(f"Catalog count mismatch: {checks}")
    if checks["invalid JSON"] or checks["duplicate ids"]:
        raise RuntimeError(f"Catalog validation failed: {checks}")


def _finite(value: Any) -> tuple[Any, bool]:
    if isinstance(value, float) and not math.isfinite(value):
        return None, True
    if isinstance(value, list):
        changed = False
        output = []
        for item in value:
            cleaned, active = _finite(item)
            changed = changed or active
            output.append(cleaned)
        return output, changed
    if isinstance(value, dict):
        changed = False
        output = {}
        for key, item in value.items():
            cleaned, active = _finite(item)
            changed = changed or active
            output[key] = cleaned
        return output, changed
    return value, False


def _load_geodata_report(path: Path | None) -> dict[str, bool]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["resource_id"]): bool(item["passed"])
        for item in payload.get("resources", [])
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
