from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![a-z0-9])[a-z]:[\\/](?:users|documents and settings)[\\/]"
    r"|(?<![a-z0-9.])/(?:home|users|root)/[^/\"']+/(?:documents|desktop|downloads|projects)/"
)


def main() -> None:
    catalog = ROOT / "catalog" / "resources.sqlite"
    manifest = json.loads(
        (ROOT / "catalog" / "manifest.json").read_text(encoding="utf-8")
    )
    digest = _sha256(catalog)
    if digest != manifest["sha256"]:
        raise RuntimeError("manifest hash does not match catalog")
    with sqlite3.connect(catalog) as connection:
        checks = {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "profiles": connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0],
            "fts": connection.execute("SELECT COUNT(*) FROM profiles_fts").fetchone()[0],
            "invalid_json": connection.execute(
                "SELECT COUNT(*) FROM profiles WHERE NOT json_valid(profile_json)"
            ).fetchone()[0],
            "duplicates": connection.execute(
                "SELECT COUNT(*) FROM (SELECT resource_id FROM profiles "
                "GROUP BY resource_id HAVING COUNT(*) > 1)"
            ).fetchone()[0],
        }
        if checks != {
            "integrity": "ok",
            "profiles": 22635,
            "fts": 22635,
            "invalid_json": 0,
            "duplicates": 0,
        }:
            raise RuntimeError(f"catalog gate failed: {checks}")
        for resource_id, raw in connection.execute(
            "SELECT resource_id, profile_json FROM profiles"
        ):
            json.loads(
                str(raw),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite JSON constant: {value}")
                ),
            )
            if ABSOLUTE_PATH.search(str(raw)):
                raise RuntimeError(
                    f"absolute development path in {resource_id}"
                )
    print(f"Catalog release gate passed: 22,635 profiles, SHA-256 {digest}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
