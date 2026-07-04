from __future__ import annotations

import tempfile
from pathlib import Path

from soriono_prelude.documents import DocumentStore

ROOT = Path(__file__).resolve().parents[1]
DOCX_RESOURCE = "85b1ca82-92dd-4252-8126-ef713fd855fb"
PDF_RESOURCE = "2e42f4c0-1ee3-46ff-b967-152e110eb8c2"


def main() -> None:
    seed = ROOT / "catalog" / "documents.sqlite"
    with tempfile.TemporaryDirectory(prefix="soriono-document-gate-") as temp:
        store = DocumentStore(
            Path(temp) / "documents.sqlite",
            seed_path=seed,
        )
        if store.status()["resource_count"] != 15_859:
            raise RuntimeError("document seed count mismatch")
        docx = store.materialize(DOCX_RESOURCE, force=True, ocr=False)
        if docx["status"] != "extracted":
            raise RuntimeError(f"DOCX extraction failed: {docx}")
        if docx["document"]["extraction_method"] != "docx_ooxml":
            raise RuntimeError(f"unexpected DOCX extraction method: {docx}")
        if not store.read(DOCX_RESOURCE, limit=1)["chunks"]:
            raise RuntimeError("DOCX extraction returned no readable chunk")
        pdf = store.materialize(PDF_RESOURCE, force=True, ocr=False)
        if pdf["status"] != "extracted":
            raise RuntimeError(f"PDF extraction failed: {pdf}")
        if pdf["document"]["extraction_method"] != "pypdf":
            raise RuntimeError(f"unexpected PDF extraction method: {pdf}")
        if int(pdf["document"]["page_count"] or 0) <= 0:
            raise RuntimeError("PDF page count is missing")
        if not store.read(PDF_RESOURCE, limit=1)["chunks"]:
            raise RuntimeError("PDF extraction returned no readable chunk")
    print("Document release gate passed: metadata, DOCX and PDF extraction")


if __name__ == "__main__":
    main()
