from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_geodata.py"
SPEC = importlib.util.spec_from_file_location("audit_geodata", SCRIPT)
assert SPEC and SPEC.loader
audit_geodata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_geodata)


def test_kml_signature_accepts_bytes_case_insensitively() -> None:
    assert audit_geodata._matches_format(
        "KML",
        b"<?xml version='1.0'?><KML xmlns='http://www.opengis.net/kml/2.2'/>",
    )


def test_geodata_binary_signatures() -> None:
    assert audit_geodata._matches_format("GPKG", b"SQLite format 3\x00rest")
    assert audit_geodata._matches_format("SHP", b"PK\x03\x04rest")
    assert audit_geodata._matches_format("SHP", b"\x00\x00\x27\x0arest")


def test_zurich_dataset_key_is_migrated_from_legacy_download_url() -> None:
    assert (
        audit_geodata._zurich_dataset_key(
            "https://www.stadt-zuerich.ch/geodaten/download/"
            "Plan_Lumiere_Konzeptplan?format=10007"
        )
        == "Plan_Lumiere_Konzeptplan"
    )


def test_opendatasoft_kml_gets_geojson_fallback() -> None:
    assert audit_geodata._opendatasoft_geojson_url(
        "https://data.bs.ch/api/v2/catalog/datasets/100234/exports/kml"
    ) == "https://data.bs.ch/api/v2/catalog/datasets/100234/exports/geojson"


def test_viageo_landing_page_gets_metadata_id() -> None:
    assert audit_geodata._viageo_metadata_id(
        "https://viageo.ch/md/eb867568-c511-4304-b902-1bbfbd0d8806#downloadAction"
    ) == "eb867568-c511-4304-b902-1bbfbd0d8806"
