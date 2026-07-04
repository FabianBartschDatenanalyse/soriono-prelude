from __future__ import annotations

import httpx
import pytest

import soriono_prelude.pxweb as pxweb


def test_unknown_scope_dimensions_are_rejected_with_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("GET", "https://example.invalid/table.px")
    response = httpx.Response(
        200,
        request=request,
        json={
            "variables": [
                {"code": "Jahr", "values": ["2024"]},
                {"code": "Kanton", "values": ["ZH"]},
            ]
        },
    )
    monkeypatch.setattr(pxweb, "_request_with_retry", lambda *args, **kwargs: response)

    with pytest.raises(pxweb.PxWebUnknownDimensions) as raised:
        pxweb.materialize_pxweb(
            str(request.url),
            scope={"Jhar": ["2024"]},
        )

    assert raised.value.unknown == ["Jhar"]
    assert raised.value.available == ["Jahr", "Kanton"]
    assert raised.value.suggestions["Jhar"] == ["Jahr"]


def test_response_text_strips_utf8_byte_order_mark() -> None:
    request = httpx.Request("POST", "https://example.invalid/table.px")
    response = httpx.Response(
        200,
        request=request,
        content="\ufeffJahr,Wert\n2024,1\n".encode(),
        headers={"content-type": "text/csv; charset=utf-8"},
    )

    assert pxweb._response_text(response) == "Jahr,Wert\n2024,1\n"
