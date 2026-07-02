from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from xml.etree import ElementTree

import httpx

GEO_FORMATS = {"GPKG", "SHP", "KML"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--catalog", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--concurrency", type=int, default=20)
    result.add_argument("--timeout", type=float, default=20)
    result.add_argument(
        "--resume",
        action="store_true",
        help="Keep successful entries from the existing report and retry failures.",
    )
    result.add_argument(
        "--max-zurich-orders",
        type=int,
        default=1,
        help="Actual Zurich end-to-end export orders sampled per run.",
    )
    result.add_argument(
        "--zurich-concurrency",
        type=int,
        default=1,
        help="Maximum concurrent Zurich export orders.",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    resources = _resources(args.catalog)
    previous = _existing_results(args.output) if args.resume else {}
    selected = _select_pending(
        resources,
        previous,
    )
    current = {
        resource["resource_id"]: previous.get(
            resource["resource_id"],
            {**resource, "passed": False, "reason": "pending_audit"},
        )
        for resource in resources
    }

    def checkpoint(item: dict[str, Any]) -> None:
        current[item["resource_id"]] = item
        _write_report(args.output, resources, current)

    report = asyncio.run(
        _audit(
            selected,
            concurrency=max(1, args.concurrency),
            timeout=max(1, args.timeout),
            zurich_concurrency=max(1, args.zurich_concurrency),
            max_zurich_orders=max(0, args.max_zurich_orders),
            on_result=checkpoint,
        )
    )
    for item in report:
        current[item["resource_id"]] = item
    payload = _write_report(args.output, resources, current)
    print(json.dumps({key: payload[key] for key in ("resource_count", "passed", "failed")}, indent=2))


def _resources(path: Path) -> list[dict[str, str]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT resource_id, format, profile_json FROM profiles "
            "WHERE upper(format) IN ('GPKG', 'SHP', 'KML')"
        ).fetchall()
    resources = []
    for resource_id, format_value, raw in rows:
        profile = json.loads(str(raw))
        source = profile.get("source") or {}
        resources.append(
            {
                "resource_id": str(resource_id),
                "format": str(format_value).upper(),
                "source_url": str(source.get("source_url") or ""),
            }
        )
    return resources


async def _audit(
    resources: list[dict[str, str]],
    *,
    concurrency: int,
    timeout: float,
    zurich_concurrency: int = 1,
    max_zurich_orders: int = 1,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(concurrency)
    zurich_semaphore = asyncio.Semaphore(min(concurrency, zurich_concurrency))
    zurich_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
    zurich_order_keys: set[str] = set()
    zurich_task_lock = asyncio.Lock()
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
        follow_redirects=True,
        limits=limits,
        headers={"User-Agent": "soriono-geodata-audit/0.3"},
    ) as client:
        async def inspect(resource: dict[str, str]) -> dict[str, Any]:
            zurich_key = _zurich_dataset_key(resource["source_url"])
            if zurich_key:
                format_code = _zurich_format_code(resource)
                task_key = f"{zurich_key}:{format_code}"
                async with zurich_task_lock:
                    task = zurich_tasks.get(task_key)
                    if task is None:
                        place_order = len(zurich_order_keys) < max_zurich_orders
                        if place_order:
                            zurich_order_keys.add(task_key)
                            task = asyncio.create_task(
                                _inspect_zurich_resolver_limited(
                                    client,
                                    resource,
                                    zurich_key,
                                    format_code,
                                    zurich_semaphore,
                                )
                            )
                        else:
                            task = asyncio.create_task(
                                _inspect_zurich_metadata_limited(
                                    client,
                                    resource,
                                    zurich_key,
                                    format_code,
                                    semaphore,
                                )
                            )
                        zurich_tasks[task_key] = task
                return {**await task, **resource}
            async with semaphore:
                return await _inspect(client, resource)

        completed: list[dict[str, Any]] = []
        tasks = [asyncio.create_task(inspect(resource)) for resource in resources]
        for task in asyncio.as_completed(tasks):
            item = await task
            completed.append(item)
            if on_result is not None:
                on_result(item)
        return completed


def _existing_results(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["resource_id"]): item
        for item in payload.get("resources", [])
        if item.get("resource_id") and item.get("passed") is True
    }


def _select_pending(
    resources: list[dict[str, str]],
    previous: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        resource
        for resource in resources
        if resource["resource_id"] not in previous
    ]


def _write_report(
    path: Path,
    resources: list[dict[str, str]],
    current: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    report = [current[resource["resource_id"]] for resource in resources]
    report.sort(key=lambda item: item["resource_id"])
    payload = {
        "resource_count": len(report),
        "passed": sum(1 for item in report if item["passed"]),
        "failed": sum(1 for item in report if not item["passed"]),
        "resources": report,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return payload


async def _inspect(
    client: httpx.AsyncClient,
    resource: dict[str, str],
) -> dict[str, Any]:
    result: dict[str, Any] = {**resource, "passed": False}
    url = resource["source_url"]
    if not url.startswith(("https://", "http://")):
        return {**result, "reason": "missing_http_url"}
    geojson_url = _opendatasoft_geojson_url(url)
    if geojson_url:
        try:
            response = await _get_with_retry(
                client,
                geojson_url,
                params={"limit": "1"},
            )
            payload = response.json()
            passed = payload.get("type") == "FeatureCollection"
            return {
                **result,
                "passed": passed,
                "reason": (
                    "provider_resolver_valid"
                    if passed
                    else "resolver_invalid_geojson"
                ),
                "resolver_type": "opendatasoft_geojson",
                "resolver_config": {"geojson_url": geojson_url},
                "resolved_url": str(response.url),
                "sample_feature_count": len(payload.get("features") or []),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                **result,
                "reason": "resolver_request_failed",
                "resolver_type": "opendatasoft_geojson",
                "resolver_config": {"geojson_url": geojson_url},
                "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
            }
    viageo_id = _viageo_metadata_id(url)
    if viageo_id:
        try:
            page = await _get_with_retry(
                client,
                "https://viageo.ch/md/" + quote(viageo_id, safe="-"),
                params={},
            )
            match = re.search(
                r'href="(?P<url>https://viageo\.ch/donnee/telecharger/[^"]+)"',
                page.text,
                flags=re.IGNORECASE,
            )
            if not match:
                raise RuntimeError("viageo exposes no direct download.")
            download_url = match.group("url").replace("&amp;", "&")
            prefix = await _first_bytes(client, download_url)
            passed = prefix.startswith(b"PK\x03\x04")
            return {
                **result,
                "passed": passed,
                "reason": (
                    "provider_resolver_valid"
                    if passed
                    else "resolver_invalid_archive"
                ),
                "resolver_type": "viageo_download",
                "resolver_config": {"metadata_id": viageo_id},
                "resolved_url": download_url,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                **result,
                "reason": "resolver_request_failed",
                "resolver_type": "viageo_download",
                "resolver_config": {"metadata_id": viageo_id},
                "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
            }
    try:
        async with client.stream("GET", url, headers={"Range": "bytes=0-8191"}) as response:
            result["http_status"] = response.status_code
            result["content_type"] = response.headers.get("content-type")
            response.raise_for_status()
            chunks = bytearray()
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) >= 8192:
                    break
        passed = _matches_format(resource["format"], bytes(chunks))
        return {
            **result,
            "passed": passed,
            "reason": "format_signature_valid" if passed else "unexpected_content",
            "resolved_url": str(response.url),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            **result,
            "reason": "request_failed",
            "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
        }


def _matches_format(format_value: str, content: bytes) -> bool:
    if format_value == "GPKG":
        return content.startswith(b"SQLite format 3\x00")
    if format_value == "KML":
        return b"<kml" in content[:8192].lower()
    if format_value == "SHP":
        return content.startswith(b"PK\x03\x04") or content.startswith(b"\x00\x00\x27\x0a")
    return False


async def _inspect_zurich_wfs(
    client: httpx.AsyncClient,
    resource: dict[str, str],
    dataset_key: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        **resource,
        "passed": False,
        "resolver_type": "stadt_zuerich_wfs",
        "resolver_config": {"dataset_key": dataset_key},
    }
    base = (
        "https://www.ogd.stadt-zuerich.ch/wfs/geoportal/"
        + quote(dataset_key, safe="_-.")
    )
    try:
        capabilities = await _get_with_retry(
            client,
            base,
            params={"SERVICE": "WFS", "REQUEST": "GetCapabilities"},
        )
        feature_types = _feature_types(capabilities.content)
        if not feature_types:
            return {**result, "reason": "resolver_no_feature_types"}
        sample = await _get_with_retry(
            client,
            base,
            params={
                "service": "WFS",
                "version": "1.1.0",
                "request": "GetFeature",
                "typeName": feature_types[0],
                "maxFeatures": "1",
                "outputFormat": "application/json",
            },
        )
        payload = sample.json()
        passed = payload.get("type") == "FeatureCollection"
        return {
            **result,
            "passed": passed,
            "reason": "provider_resolver_valid" if passed else "resolver_invalid_geojson",
            "resolved_url": base,
            "feature_type_count": len(feature_types),
            "sample_feature_count": len(payload.get("features") or []),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            **result,
            "reason": "resolver_request_failed",
            "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
            "resolved_url": base,
        }


async def _inspect_zurich_resolver_limited(
    client: httpx.AsyncClient,
    resource: dict[str, str],
    dataset_key: str,
    format_code: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        return await _inspect_zurich_order(
            client,
            resource,
            dataset_key,
            format_code,
        )


async def _inspect_zurich_metadata_limited(
    client: httpx.AsyncClient,
    resource: dict[str, str],
    dataset_key: str,
    format_code: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        result: dict[str, Any] = {
            **resource,
            "passed": False,
            "resolver_type": "stadt_zuerich_order",
            "resolver_config": {
                "dataset_key": dataset_key,
                "format_code": format_code,
            },
        }
        try:
            page = await _get_with_retry(
                client,
                "https://www.ogd.stadt-zuerich.ch/geodaten/download/"
                + quote(dataset_key, safe="_-."),
                params={},
            )
            identifier = re.search(r"&q;id&q;:(\d+)", page.text)
            name = re.search(r"&q;geoportal_name&q;:&q;(.*?)&q;", page.text)
            if not identifier or not name:
                raise RuntimeError("Zurich order metadata is incomplete.")
            if format_code not in {"10005", "10007"}:
                raise RuntimeError(
                    f"Unsupported Zurich format code: {format_code}"
                )
            return {
                **result,
                "passed": True,
                "reason": "provider_resolver_metadata_valid",
                "resolved_url": str(page.url),
                "provider_dataset_id": int(identifier.group(1)),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                **result,
                "reason": "resolver_metadata_failed",
                "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
            }


async def _inspect_zurich_order(
    client: httpx.AsyncClient,
    resource: dict[str, str],
    dataset_key: str,
    format_code: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        **resource,
        "passed": False,
        "resolver_type": "stadt_zuerich_order",
        "resolver_config": {
            "dataset_key": dataset_key,
            "format_code": format_code,
        },
    }
    try:
        page = await _get_with_retry(
            client,
            "https://www.ogd.stadt-zuerich.ch/geodaten/download/"
            + quote(dataset_key, safe="_-."),
            params={},
        )
        identifier = re.search(r"&q;id&q;:(\d+)", page.text)
        name = re.search(r"&q;geoportal_name&q;:&q;(.*?)&q;", page.text)
        if not identifier or not name:
            raise RuntimeError("Zurich order metadata is incomplete.")
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
        }[format_code]
        response = await client.post(
            "https://www.ogd.stadt-zuerich.ch/geoportal_order/",
            json={
                "order": {
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
            },
            headers={
                "Content-Type": "application/json",
                "Origin": "https://www.ogd.stadt-zuerich.ch",
                "Referer": "https://www.ogd.stadt-zuerich.ch/",
            },
        )
        response.raise_for_status()
        order = response.json()
        job_id = str(order.get("job_id") or "")
        download_url = str(order.get("download_url") or "")
        if not job_id or not download_url:
            raise RuntimeError("Zurich order response is incomplete.")
        deadline = asyncio.get_running_loop().time() + 240
        status = ""
        while asyncio.get_running_loop().time() < deadline:
            status_response = await client.get(
                "https://www.ogd.stadt-zuerich.ch/geoportal_order/" + job_id
            )
            status_response.raise_for_status()
            status = str(status_response.json().get("status") or "")
            if "SUCCESS" in status:
                break
            if any(value in status for value in ("FAILURE", "ABORTED", "NOT FOUND")):
                raise RuntimeError(f"Zurich order failed with status {status}.")
            await asyncio.sleep(2)
        else:
            raise RuntimeError("Zurich order timed out.")
        archive_prefix = await _first_bytes(client, download_url)
        passed = archive_prefix.startswith(b"PK\x03\x04")
        return {
            **result,
            "passed": passed,
            "reason": "provider_order_valid" if passed else "resolver_invalid_archive",
            "job_status": status,
            "resolved_url": (
                "https://www.ogd.stadt-zuerich.ch/geoportal_download/"
                f"{job_id}.zip"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            **result,
            "reason": "resolver_order_failed",
            "error": f"{exc.__class__.__name__}: {str(exc)[:500]}",
        }


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str],
) -> httpx.Response:
    error: Exception | None = None
    for attempt in range(3):
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            error = exc
            if attempt < 2:
                await asyncio.sleep(0.5 * (2**attempt))
    assert error is not None
    raise error


async def _first_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    limit: int = 8192,
) -> bytes:
    content = bytearray()
    async with client.stream(
        "GET",
        url,
        headers={"Range": f"bytes=0-{limit - 1}"},
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) >= limit:
                break
    return bytes(content)


def _zurich_dataset_key(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    if parsed.hostname not in {"www.stadt-zuerich.ch", "www.ogd.stadt-zuerich.ch"}:
        return None
    marker = "/geodaten/download/"
    if marker not in parsed.path:
        return None
    return parsed.path.split(marker, 1)[1].strip("/") or None


def _opendatasoft_geojson_url(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    match = re.fullmatch(
        r"(?P<prefix>/api/v2/catalog/datasets/[^/]+/exports/)"
        r"(?:kml|shp|gpkg)",
        parsed.path,
        flags=re.IGNORECASE,
    )
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not match:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{match.group('prefix')}geojson"


def _viageo_metadata_id(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    if parsed.hostname != "viageo.ch" or not parsed.path.startswith("/md/"):
        return None
    return parsed.path.split("/md/", 1)[1].strip("/") or None


def _zurich_format_code(resource: dict[str, str]) -> str:
    parsed = urlparse(resource["source_url"])
    configured = (parse_qs(parsed.query).get("format") or [""])[0]
    if configured in {"10005", "10007"}:
        return configured
    return "10005" if resource["format"] == "GPKG" else "10007"


def _feature_types(content: bytes) -> list[str]:
    root = ElementTree.fromstring(content)
    names: list[str] = []
    for element in root.iter():
        if element.tag.endswith("FeatureType"):
            for child in element:
                if child.tag.endswith("Name") and child.text:
                    names.append(child.text.strip())
                    break
    return list(dict.fromkeys(item for item in names if item))


if __name__ == "__main__":
    main()
