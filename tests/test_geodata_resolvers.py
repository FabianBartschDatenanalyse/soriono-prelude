from __future__ import annotations

from soriono_prelude.geodata_resolvers import (
    OPENDATASOFT_RESOLVER,
    VIAGEO_RESOLVER,
    ZURICH_RESOLVER,
    infer_resolver,
)
from soriono_prelude.sources import SourceRecord

LEGACY_URL = (
    "https://www.stadt-zuerich.ch/geodaten/download/"
    "Plan_Lumiere_Konzeptplan?format=10007"
)


def test_legacy_zurich_url_gets_deterministic_resolver() -> None:
    resolver_type, config = infer_resolver(LEGACY_URL)

    assert resolver_type == ZURICH_RESOLVER
    assert config == {
        "dataset_key": "Plan_Lumiere_Konzeptplan",
        "format_code": "10007",
    }


def test_old_registry_record_is_migrated_on_load() -> None:
    source = SourceRecord(
        source_handle="source:legacy",
        resource_id="legacy",
        title="Legacy",
        source_url=LEGACY_URL,
        duckdb_reader=f"ST_Read('{LEGACY_URL}')",
        format="SHP",
    )

    assert source.resolver_type == ZURICH_RESOLVER
    assert source.resolver_config == {
        "dataset_key": "Plan_Lumiere_Konzeptplan",
        "format_code": "10007",
    }


def test_opendatasoft_geodata_uses_geojson_fallback() -> None:
    resolver_type, config = infer_resolver(
        "https://data.bs.ch/api/v2/catalog/datasets/100234/exports/kml"
    )

    assert resolver_type == OPENDATASOFT_RESOLVER
    assert config == {
        "geojson_url": (
            "https://data.bs.ch/api/v2/catalog/datasets/100234/exports/geojson"
        )
    }


def test_viageo_landing_page_gets_download_resolver() -> None:
    resolver_type, config = infer_resolver(
        "https://viageo.ch/md/eb867568-c511-4304-b902-1bbfbd0d8806#downloadAction"
    )

    assert resolver_type == VIAGEO_RESOLVER
    assert config == {"metadata_id": "eb867568-c511-4304-b902-1bbfbd0d8806"}
