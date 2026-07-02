from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from soriono_prelude.catalog_updates import install_catalog


def _catalog(path: Path, resource_id: str) -> str:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE profiles (
            resource_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            publisher TEXT,
            source_system TEXT,
            format TEXT,
            workflow_smoke_passed INTEGER NOT NULL,
            profile_json TEXT NOT NULL
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    connection.execute(
        "INSERT INTO profiles VALUES (?, ?, '', '', '', 1, ?)",
        (resource_id, resource_id, json.dumps({"resource_id": resource_id})),
    )
    connection.execute("INSERT INTO metadata VALUES ('catalog_version', ?)", (json.dumps("test"),))
    connection.commit()
    connection.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_catalog_update_verifies_and_keeps_previous(tmp_path: Path) -> None:
    source = tmp_path / "release.sqlite"
    digest = _catalog(source, "new")
    target = tmp_path / "catalog" / "resources.sqlite"
    target.parent.mkdir()
    _catalog(target, "old")

    result = install_catalog(str(source), sha256=digest, destination=target)

    assert result["status"] == "installed"
    assert result["resource_count"] == 1
    assert target.is_file()
    assert target.with_suffix(".sqlite.previous").is_file()


def test_catalog_update_rejects_bad_checksum(tmp_path: Path) -> None:
    source = tmp_path / "release.sqlite"
    _catalog(source, "new")

    with pytest.raises(ValueError, match="checksum mismatch"):
        install_catalog(str(source), sha256="0" * 64, destination=tmp_path / "resources.sqlite")
