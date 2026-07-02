from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from xml.etree import ElementTree

import duckdb
import httpx

from soriono_prelude.catalog import state_dir
from soriono_prelude.sources import SourceRecord, sql_literal

ZURICH_RESOLVER = "stadt_zuerich_order"
OPENDATASOFT_RESOLVER = "opendatasoft_geojson"
VIAGEO_RESOLVER = "viageo_download"
DEFAULT_MAX_GEODATA_BYTES = 500_000_000
ZURICH_ORDER_URL = "https://www.ogd.stadt-zuerich.ch/geoportal_order/"


class GeodataResolverUnavailable(RuntimeError):
    pass


def infer_resolver(source_url: str) -> tuple[str | None, dict[str, str]]:
    parsed = urlparse(source_url)
    if parsed.hostname in {"www.stadt-zuerich.ch", "www.ogd.stadt-zuerich.ch"}:
        marker = "/geodaten/download/"
        if marker in parsed.path:
            dataset_key = parsed.path.split(marker, 1)[1].strip("/")
            if dataset_key:
                format_code = (parse_qs(parsed.query).get("format") or [""])[0]
                config = {"dataset_key": dataset_key}
                if format_code:
                    config["format_code"] = format_code
                return ZURICH_RESOLVER, config
    match = re.fullmatch(
        r"(?P<prefix>/api/v2/catalog/datasets/[^/]+/exports/)"
        r"(?:kml|shp|gpkg)",
        parsed.path,
        flags=re.IGNORECASE,
    )
    if parsed.scheme in {"http", "https"} and parsed.hostname and match:
        return OPENDATASOFT_RESOLVER, {
            "geojson_url": (
                f"{parsed.scheme}://{parsed.netloc}{match.group('prefix')}geojson"
            )
        }
    if parsed.hostname == "viageo.ch" and parsed.path.startswith("/md/"):
        metadata_id = parsed.path.split("/md/", 1)[1].strip("/")
        if metadata_id:
            return VIAGEO_RESOLVER, {"metadata_id": metadata_id}
    return None, {}


def materialize_resolved_source(
    source: SourceRecord,
    connection: duckdb.DuckDBPyConnection,
) -> Path | None:
    if source.resolver_type == OPENDATASOFT_RESOLVER:
        return _materialize_opendatasoft(source, connection)
    if source.resolver_type == VIAGEO_RESOLVER:
        return _materialize_viageo(source, connection)
    if source.resolver_type != ZURICH_RESOLVER:
        return None
    dataset_key = str(source.resolver_config.get("dataset_key") or "").strip()
    if not dataset_key:
        raise GeodataResolverUnavailable("The Zurich WFS resolver has no dataset key.")
    try:
        return _materialize_zurich_order(source, dataset_key, connection)
    except GeodataResolverUnavailable:
        return _materialize_zurich_wfs(source, dataset_key, connection)


def zurich_wfs_base(dataset_key: str) -> str:
    return (
        "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/"
        + quote(dataset_key, safe="_-.")
    )


def _materialize_opendatasoft(
    source: SourceRecord,
    connection: duckdb.DuckDBPyConnection,
) -> Path:
    geojson_url = str(source.resolver_config.get("geojson_url") or "").strip()
    if not geojson_url.startswith(("https://", "http://")):
        raise GeodataResolverUnavailable(
            "The OpenDataSoft resolver has no valid GeoJSON URL."
        )
    digest = hashlib.sha256(
        f"{source.resource_id}\n{geojson_url}".encode()
    ).hexdigest()[:24]
    output_dir = state_dir() / "geodata"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{digest}.parquet"
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    geojson = output_dir / f"{digest}.geojson"
    geojson_tmp = geojson.with_suffix(".geojson.tmp")
    parquet_tmp = destination.with_suffix(".parquet.tmp")
    maximum_bytes = max(
        1,
        int(
            os.environ.get(
                "SORIONO_PRELUDE_MAX_GEODATA_BYTES",
                DEFAULT_MAX_GEODATA_BYTES,
            )
        ),
    )
    downloaded = 0
    try:
        with httpx.stream(
            "GET",
            geojson_url,
            timeout=httpx.Timeout(180, connect=30),
            follow_redirects=True,
            headers={"User-Agent": "soriono-prelude/0.3"},
        ) as response:
            response.raise_for_status()
            with geojson_tmp.open("wb") as output:
                for chunk in response.iter_bytes():
                    downloaded += len(chunk)
                    if downloaded > maximum_bytes:
                        raise GeodataResolverUnavailable(
                            "Resolved OpenDataSoft geodata exceeds the configured "
                            f"download limit: {maximum_bytes} bytes"
                        )
                    output.write(chunk)
        os.replace(geojson_tmp, geojson)
        connection.execute(
            f"COPY (SELECT * FROM ST_Read({sql_literal(geojson.as_posix())})) "
            f"TO {sql_literal(parquet_tmp.as_posix())} (FORMAT PARQUET)"
        )
        os.replace(parquet_tmp, destination)
    except Exception as exc:
        geojson_tmp.unlink(missing_ok=True)
        parquet_tmp.unlink(missing_ok=True)
        if isinstance(exc, GeodataResolverUnavailable):
            raise
        raise GeodataResolverUnavailable(
            f"OpenDataSoft GeoJSON resolution failed for {geojson_url}."
        ) from exc
    finally:
        geojson.unlink(missing_ok=True)
    return destination


def _materialize_viageo(
    source: SourceRecord,
    connection: duckdb.DuckDBPyConnection,
) -> Path:
    metadata_id = str(source.resolver_config.get("metadata_id") or "").strip()
    if not metadata_id:
        raise GeodataResolverUnavailable(
            "The viageo resolver has no metadata identifier."
        )
    digest = hashlib.sha256(
        f"{source.resource_id}\n{metadata_id}\nviageo".encode()
    ).hexdigest()[:24]
    output_dir = state_dir() / "geodata"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{digest}.parquet"
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    archive = output_dir / f"{digest}.zip"
    temporary = destination.with_suffix(".parquet.tmp")
    maximum_bytes = max(
        1,
        int(
            os.environ.get(
                "SORIONO_PRELUDE_MAX_GEODATA_BYTES",
                DEFAULT_MAX_GEODATA_BYTES,
            )
        ),
    )
    try:
        with httpx.Client(
            timeout=httpx.Timeout(180, connect=30),
            follow_redirects=True,
            headers={"User-Agent": "soriono-prelude/0.3"},
        ) as client:
            page = client.get(
                "https://viageo.ch/md/" + quote(metadata_id, safe="-")
            )
            page.raise_for_status()
            match = re.search(
                r'href="(?P<url>https://viageo\.ch/donnee/telecharger/[^"]+)"',
                page.text,
                flags=re.IGNORECASE,
            )
            if not match:
                raise GeodataResolverUnavailable(
                    f"viageo exposes no direct download for {metadata_id}."
                )
            _download_archive(
                client,
                match.group("url").replace("&amp;", "&"),
                archive,
                maximum_bytes,
            )
        dataset_path = _archive_any_dataset_path(archive, source.format)
        metadata_row = connection.execute(
            f"SELECT layers FROM ST_Read_Meta({sql_literal(dataset_path)})"
        ).fetchone()
        layers = [
            str(item["name"])
            for item in (metadata_row[0] if metadata_row else [])
            if item.get("name")
        ]
        if not layers:
            raise GeodataResolverUnavailable(
                "viageo archive exposes no readable geodata layers."
            )
        selects = [
            (
                f"SELECT *, {sql_literal(layer)} AS _soriono_layer "
                f"FROM ST_Read({sql_literal(dataset_path)}, "
                f"layer={sql_literal(layer)})"
            )
            for layer in layers
        ]
        connection.execute(
            f"COPY ({' UNION ALL BY NAME '.join(selects)}) "
            f"TO {sql_literal(temporary.as_posix())} (FORMAT PARQUET)"
        )
        os.replace(temporary, destination)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        if isinstance(exc, GeodataResolverUnavailable):
            raise
        raise GeodataResolverUnavailable(
            f"viageo resolution failed for {metadata_id}."
        ) from exc
    archive.unlink(missing_ok=True)
    return destination


def _materialize_zurich_order(
    source: SourceRecord,
    dataset_key: str,
    connection: duckdb.DuckDBPyConnection,
) -> Path:
    digest = hashlib.sha256(
        f"{source.resource_id}\n{dataset_key}\norder".encode()
    ).hexdigest()[:24]
    output_dir = state_dir() / "geodata"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{digest}.parquet"
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    archive = output_dir / f"{digest}.zip"
    temporary = destination.with_suffix(".parquet.tmp")
    maximum_bytes = max(
        1,
        int(
            os.environ.get(
                "SORIONO_PRELUDE_MAX_GEODATA_BYTES",
                DEFAULT_MAX_GEODATA_BYTES,
            )
        ),
    )
    try:
        with httpx.Client(
            timeout=httpx.Timeout(120, connect=30),
            follow_redirects=True,
            headers={
                "User-Agent": "soriono-prelude/0.3",
                "Origin": "https://www.ogd.stadt-zuerich.ch",
                "Referer": "https://www.ogd.stadt-zuerich.ch/",
            },
        ) as client:
            metadata = _zurich_order_metadata(client, dataset_key, source)
            response = client.post(
                ZURICH_ORDER_URL,
                json={"order": metadata},
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            order = response.json()
            job_id = str(order.get("job_id") or "")
            download_url = str(order.get("download_url") or "")
            if not job_id or not download_url:
                raise GeodataResolverUnavailable(
                    "Zurich order response contains no job or download URL."
                )
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                status_response = client.get(ZURICH_ORDER_URL + job_id)
                status_response.raise_for_status()
                status = str(status_response.json().get("status") or "")
                if "SUCCESS" in status:
                    break
                if any(value in status for value in ("FAILURE", "ABORTED", "NOT FOUND")):
                    raise GeodataResolverUnavailable(
                        f"Zurich geodata order failed with status {status}."
                    )
                time.sleep(2)
            else:
                raise GeodataResolverUnavailable("Zurich geodata order timed out.")
            _download_archive(client, download_url, archive, maximum_bytes)
        dataset_path = _archive_dataset_path(archive, source.format)
        metadata_row = connection.execute(
            f"SELECT layers FROM ST_Read_Meta({sql_literal(dataset_path)})"
        ).fetchone()
        layers = [
            str(item["name"])
            for item in (metadata_row[0] if metadata_row else [])
            if item.get("name")
        ]
        if not layers:
            raise GeodataResolverUnavailable(
                "Zurich order archive exposes no readable geodata layers."
            )
        selects = [
            (
                f"SELECT *, {sql_literal(layer)} AS _soriono_layer "
                f"FROM ST_Read({sql_literal(dataset_path)}, "
                f"layer={sql_literal(layer)})"
            )
            for layer in layers
        ]
        connection.execute(
            f"COPY ({' UNION ALL BY NAME '.join(selects)}) "
            f"TO {sql_literal(temporary.as_posix())} (FORMAT PARQUET)"
        )
        os.replace(temporary, destination)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        archive.unlink(missing_ok=True)
        if isinstance(exc, GeodataResolverUnavailable):
            raise
        raise GeodataResolverUnavailable(
            f"Zurich order resolution failed for {dataset_key}."
        ) from exc
    archive.unlink(missing_ok=True)
    return destination


def _zurich_order_metadata(
    client: httpx.Client,
    dataset_key: str,
    source: SourceRecord,
) -> dict[str, object]:
    page = client.get(
        "https://www.ogd.stadt-zuerich.ch/geodaten/download/"
        + quote(dataset_key, safe="_-.")
    )
    page.raise_for_status()
    identifier = re.search(r"&q;id&q;:(\d+)", page.text)
    name = re.search(r"&q;geoportal_name&q;:&q;(.*?)&q;", page.text)
    if not identifier or not name:
        raise GeodataResolverUnavailable(
            f"Could not read Zurich order metadata for {dataset_key}."
        )
    format_code = str(source.resolver_config.get("format_code") or "")
    if not format_code:
        format_code = {
            "gpkg": "10005",
            "shp": "10007",
        }.get(str(source.format or "").casefold(), "10005")
    format_payload = {
        "10005": {
            "id": 10005,
            "name": "Geopackage (.gpkg)",
            "fme_name": "GEOPACKAGE",
        },
        "10007": {
            "id": 10007,
            "name": "ESRI Shape (.shp)",
            "fme_name": "ESRISHAPE",
        },
    }.get(format_code)
    if format_payload is None:
        raise GeodataResolverUnavailable(
            f"Unsupported Zurich order format code: {format_code}"
        )
    return {
        "selected_format": format_payload,
        "extent_name": "full",
        "extent": None,
        "inkl3d": {"value": 0, "text": "nur 2D"},
        "stzh": True,
        "geoportal_name": name.group(1),
        "geoportal_id": dataset_key,
        "geodatensatz_id": int(identifier.group(1)),
        "email": "",
    }


def _download_archive(
    client: httpx.Client,
    url: str,
    destination: Path,
    maximum_bytes: int,
) -> None:
    temporary = destination.with_suffix(".zip.tmp")
    downloaded = 0
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    downloaded += len(chunk)
                    if downloaded > maximum_bytes:
                        raise GeodataResolverUnavailable(
                            "Zurich geodata archive exceeds the configured "
                            f"download limit: {maximum_bytes} bytes"
                        )
                    output.write(chunk)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _archive_dataset_path(archive: Path, format_value: str | None) -> str:
    preferred = (
        ".gpkg" if str(format_value or "").casefold() == "gpkg" else ".shp"
    )
    with zipfile.ZipFile(archive) as file:
        members = [
            item.filename
            for item in file.infolist()
            if not item.is_dir() and item.filename.casefold().endswith(preferred)
        ]
    if not members:
        raise GeodataResolverUnavailable(
            f"Zurich order archive contains no {preferred} dataset."
        )
    return f"/vsizip/{archive.as_posix()}/{members[0]}"


def _archive_any_dataset_path(
    archive: Path,
    format_value: str | None,
) -> str:
    preferred = (
        ".gpkg" if str(format_value or "").casefold() == "gpkg" else ".shp"
    )
    suffixes = (preferred, ".shp" if preferred == ".gpkg" else ".gpkg")
    with zipfile.ZipFile(archive) as file:
        filenames = [
            item.filename
            for item in file.infolist()
            if not item.is_dir()
        ]
    for suffix in suffixes:
        members = [
            filename
            for filename in filenames
            if filename.casefold().endswith(suffix)
        ]
        if members:
            return f"/vsizip/{archive.as_posix()}/{members[0]}"
    raise GeodataResolverUnavailable(
        "viageo archive contains no GPKG or SHP dataset."
    )


def _materialize_zurich_wfs(
    source: SourceRecord,
    dataset_key: str,
    connection: duckdb.DuckDBPyConnection,
) -> Path:
    digest = hashlib.sha256(
        f"{source.resource_id}\n{dataset_key}".encode()
    ).hexdigest()[:24]
    output_dir = state_dir() / "geodata"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{digest}.parquet"
    if destination.is_file() and destination.stat().st_size > 0:
        return destination
    temporary = destination.with_suffix(".parquet.tmp")
    layer_paths: list[tuple[str, Path]] = []
    maximum_bytes = max(
        1,
        int(
            os.environ.get(
                "SORIONO_PRELUDE_MAX_GEODATA_BYTES",
                DEFAULT_MAX_GEODATA_BYTES,
            )
        ),
    )
    downloaded = 0
    base = zurich_wfs_base(dataset_key)
    try:
        with httpx.Client(
            timeout=httpx.Timeout(180, connect=30),
            follow_redirects=True,
            headers={"User-Agent": "soriono-prelude/0.3"},
        ) as client:
            capabilities = client.get(
                base,
                params={"SERVICE": "WFS", "REQUEST": "GetCapabilities"},
            )
            capabilities.raise_for_status()
            feature_types = _feature_types(capabilities.content)
            if not feature_types:
                raise GeodataResolverUnavailable(
                    f"Zurich WFS exposes no feature types for {dataset_key}."
                )
            for index, feature_type in enumerate(feature_types):
                layer_path = output_dir / f"{digest}-{index}.geojson"
                layer_tmp = layer_path.with_suffix(".geojson.tmp")
                with client.stream(
                    "GET",
                    base,
                    params={
                        "service": "WFS",
                        "version": "1.1.0",
                        "request": "GetFeature",
                        "typeName": feature_type,
                        "outputFormat": "application/json",
                    },
                ) as response:
                    response.raise_for_status()
                    with layer_tmp.open("wb") as output:
                        for chunk in response.iter_bytes():
                            downloaded += len(chunk)
                            if downloaded > maximum_bytes:
                                raise GeodataResolverUnavailable(
                                    "Resolved geodata exceeds the configured "
                                    f"download limit: {maximum_bytes} bytes"
                                )
                            output.write(chunk)
                os.replace(layer_tmp, layer_path)
                layer_paths.append((feature_type, layer_path))
        selects = [
            (
                f"SELECT *, {sql_literal(feature_type)} AS _soriono_layer "
                f"FROM ST_Read({sql_literal(path.as_posix())})"
            )
            for feature_type, path in layer_paths
        ]
        connection.execute(
            f"COPY ({' UNION ALL BY NAME '.join(selects)}) "
            f"TO {sql_literal(temporary.as_posix())} (FORMAT PARQUET)"
        )
        os.replace(temporary, destination)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        for _, path in layer_paths:
            path.unlink(missing_ok=True)
            path.with_suffix(".geojson.tmp").unlink(missing_ok=True)
        if isinstance(exc, GeodataResolverUnavailable):
            raise
        raise GeodataResolverUnavailable(
            f"Zurich WFS resolution failed for {dataset_key}."
        ) from exc
    for _, path in layer_paths:
        path.unlink(missing_ok=True)
    return destination


def _feature_types(content: bytes) -> list[str]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise GeodataResolverUnavailable("Invalid Zurich WFS capabilities XML.") from exc
    names: list[str] = []
    for element in root.iter():
        if element.tag.endswith("FeatureType"):
            for child in element:
                if child.tag.endswith("Name") and child.text:
                    names.append(child.text.strip())
                    break
    return list(dict.fromkeys(item for item in names if item))
