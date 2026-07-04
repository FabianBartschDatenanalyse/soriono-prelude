from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from soriono_prelude.catalog import Catalog, catalog_path, state_dir
from soriono_prelude.catalog_updates import install_catalog
from soriono_prelude.documents import DocumentStore
from soriono_prelude.server import run_server


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="soriono-prelude")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("mcp-server")
    commands.add_parser("doctor")
    search = commands.add_parser("search")
    search.add_argument("question")
    search.add_argument("--top-k", type=int, default=10)
    commands.add_parser("catalog-status")
    update = commands.add_parser("install-catalog")
    update.add_argument("source", help="Local file path or HTTPS URL")
    update.add_argument("--sha256", required=True)
    sync_documents = commands.add_parser("sync-documents")
    sync_documents.add_argument(
        "--formats",
        nargs="+",
        help="Subset of PDF DOC DOCX ODT RTF HTML HTM",
    )
    return root


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.command == "mcp-server":
        run_server()
        return
    if args.command == "install-catalog":
        _print(install_catalog(args.source, sha256=args.sha256))
        return
    if args.command == "sync-documents":
        _print(DocumentStore().sync_opendata_swiss(formats=args.formats))
        return
    catalog = Catalog()
    if args.command == "doctor":
        _print(
            {
                "status": "ok",
                "catalog": catalog.status(),
                "catalog_path": str(catalog_path()),
                "state_dir": str(state_dir()),
                "search_engine": "sqlite_fts5",
                "internal_llm_calls": False,
                "vespa_required": False,
                "docker_required": False,
            }
        )
        return
    if args.command == "catalog-status":
        _print(catalog.status())
        return
    if args.command == "search":
        _print(
            {
                "question": args.question,
                "hits": [
                    {
                        "score": hit.score,
                        "matched_terms": hit.matched_terms,
                        "resource_id": hit.profile.get("resource_id"),
                        "title": hit.profile.get("title"),
                        "publisher": hit.profile.get("publisher"),
                    }
                    for hit in catalog.search(args.question, top_k=args.top_k)
                ],
            }
        )


def _print(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
