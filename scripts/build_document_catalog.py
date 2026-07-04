from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from soriono_prelude.documents import DocumentStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parents[1] / "catalog" / "documents.sqlite",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.unlink(missing_ok=True)
    store = DocumentStore(args.output)
    result = store.sync_opendata_swiss(page_size=500)
    with sqlite3.connect(args.output) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"Document catalog integrity check failed: {integrity}")
    print(json.dumps({**result, **store.status()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
