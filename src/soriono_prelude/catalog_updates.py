from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from soriono_prelude.catalog import catalog_path


def install_catalog(
    source: str,
    *,
    sha256: str,
    destination: Path | None = None,
) -> dict[str, Any]:
    expected = sha256.strip().lower()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("sha256 must be a 64-character hexadecimal digest")

    target = (destination or catalog_path()).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="soriono-prelude-catalog-", dir=target.parent) as temp_dir:
        candidate = Path(temp_dir) / "resources.sqlite"
        _copy_source(source, candidate)
        actual = _sha256(candidate)
        if actual != expected:
            raise ValueError(f"Catalog checksum mismatch: expected {expected}, got {actual}")
        metadata = _validate_catalog(candidate)

        staged = target.with_suffix(".sqlite.new")
        shutil.copyfile(candidate, staged)
        if target.exists():
            backup = target.with_suffix(".sqlite.previous")
            os.replace(target, backup)
        os.replace(staged, target)

    return {
        "status": "installed",
        "path": str(target),
        "sha256": actual,
        **metadata,
    }


def _copy_source(source: str, destination: Path) -> None:
    parsed = urlparse(source)
    is_windows_path = len(parsed.scheme) == 1 and len(source) >= 3 and source[1:3] in {":\\", ":/"}
    if parsed.scheme and not is_windows_path:
        if parsed.scheme != "https":
            raise ValueError("Remote catalog source must use HTTPS")
        with httpx.stream("GET", source, timeout=120, follow_redirects=True) as response:
            response.raise_for_status()
            with destination.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        return
    local = Path(source).expanduser().resolve()
    if not local.is_file():
        raise FileNotFoundError(f"Catalog source does not exist: {local}")
    shutil.copyfile(local, destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_catalog(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ValueError(f"Catalog integrity check failed: {integrity}")
        count = int(connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0])
        metadata = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key, value FROM metadata")
        }
    except sqlite3.Error as exc:
        raise ValueError(f"Invalid Soriono Prelude catalog: {exc}") from exc
    finally:
        connection.close()
    if count <= 0:
        raise ValueError("Catalog contains no resource profiles")
    return {
        "resource_count": count,
        "catalog_version": metadata.get("catalog_version"),
    }
