from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from soriono_prelude.duckdb_runtime import load_spatial, open_connection
from soriono_prelude.sources import SourceRecord


@pytest.mark.parametrize(
    ("extension", "driver"),
    [("gpkg", "GPKG"), ("shp", "ESRI Shapefile"), ("kml", "KML")],
)
def test_spatial_format_can_be_loaded(
    tmp_path: Path,
    extension: str,
    driver: str,
) -> None:
    path = tmp_path / f"sample.{extension}"
    writer = duckdb.connect(":memory:")
    try:
        load_spatial(writer)
        writer.execute(
            "COPY (SELECT 1 AS id, ST_Point(8.54, 47.37) AS geom) "
            f"TO '{path.as_posix()}' WITH (FORMAT GDAL, DRIVER '{driver}')"
        )
    finally:
        writer.close()
    source = SourceRecord(
        source_handle=f"source:test:{extension}",
        resource_id=f"test-{extension}",
        title=f"Test {extension}",
        source_url=path.as_posix(),
        duckdb_reader=f"ST_Read('{path.as_posix()}')",
        format=extension,
        access_method="test",
    )

    connection = open_connection([source])
    try:
        assert connection.execute(f"SELECT COUNT(*) FROM {source.sql_name}").fetchone() == (1,)
    finally:
        connection.close()
