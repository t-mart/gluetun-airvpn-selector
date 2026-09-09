from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import httpx2
import pytest
from starlette.applications import Starlette

from gluetun_airvpn_selector.domain import Selection, parse_catalog
from gluetun_airvpn_selector.web import _format_bandwidth_usage, _options, create_app
from tests.test_domain import api_server
from tests.test_services import config


def test_app_requires_api_key() -> None:
    with pytest.raises(ValueError, match="GLUETUN_API_KEY"):
        create_app(config(gluetun_api_key=""))


@asynccontextmanager
async def browser_for(
    *,
    api_key: str = "secret",
    gluetun_status: int = 200,
    authenticated: bool = True,
    selection: Selection | None = None,
    servers: list[dict[str, object]] | None = None,
) -> AsyncIterator[tuple[httpx2.AsyncClient, list[httpx2.Request]]]:
    requests: list[httpx2.Request] = []
    selection = selection if selection is not None else Selection(countries=("Canada",))

    def upstream(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.host == "airvpn.test":
            return httpx2.Response(
                200,
                json={"servers": servers if servers is not None else [api_server()]},
            )
        if gluetun_status != 200:
            return httpx2.Response(gluetun_status)
        if request.url.path == "/v1/vpn/status":
            return httpx2.Response(200, json={"status": "running"})
        if request.url.path == "/v1/publicip/ip":
            return httpx2.Response(200, json={"public_ip": "203.0.113.8"})
        if request.method == "PUT":
            return httpx2.Response(200, text="VPN settings changed")
        return httpx2.Response(
            200,
            json={
                "provider": {
                    "name": "airvpn",
                    "server_selection": selection.to_dict(),
                }
            },
        )

    upstream_client = httpx2.AsyncClient(transport=httpx2.MockTransport(upstream))
    app: Starlette = create_app(config(gluetun_api_key=api_key), upstream_client)
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as browser:
            if authenticated:
                response = await browser.post(
                    "/login",
                    content=f"api_key={api_key}",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                assert response.status_code == 303
                requests.clear()
            yield browser, requests


@pytest.mark.anyio
async def test_homepage_renders_state_and_security_headers() -> None:
    async with browser_for() as (browser, _):
        response = await browser.get("/")

    assert response.status_code == 200
    assert "Agena" in response.text
    assert "203.0.113.8" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "Eligible servers" not in response.text
    assert "Choose AirVPN servers" not in response.text
    assert response.text.count('data-filter="names"') == 1
    assert response.text.count('data-state="implied"') == 3
    assert response.text.count('data-state="selected"') == 1
    assert "1 of 2 Gbps utilized" in response.text


@pytest.mark.anyio
async def test_login_without_origin_uses_an_opaque_cookie() -> None:
    async with browser_for(authenticated=False) as (browser, requests):
        unauthenticated = await browser.get("/")
        response = await browser.post(
            "/login",
            content="api_key=secret",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        homepage = await browser.get("/")

    assert unauthenticated.status_code == 303
    assert response.status_code == 303
    assert homepage.status_code == 200
    assert "secret" not in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    gluetun_requests = [
        request for request in requests if request.url.host == "gluetun"
    ]
    assert gluetun_requests
    assert all(request.headers["X-API-Key"] == "secret" for request in gluetun_requests)


@pytest.mark.anyio
async def test_state_change_without_origin_updates_gluetun() -> None:
    async with browser_for() as (browser, requests):
        response = await browser.put(
            "/api/selection",
            json={"names": [], "countries": [], "cities": [], "regions": []},
        )

    assert response.status_code == 200
    assert any(request.method == "PUT" for request in requests)


@pytest.mark.anyio
async def test_empty_intersection_stops_upstream_update() -> None:
    payload: dict[str, Any] = {
        "names": [],
        "countries": ["United States"],
        "cities": ["Toronto Ontario"],
        "regions": [],
    }
    async with browser_for() as (browser, requests):
        response = await browser.put(
            "/api/selection",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_selection"
    assert not any(request.method == "PUT" for request in requests)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "valid", "orphan"),
    [
        ("names", "Agena", "Missing"),
        ("countries", "Canada", "Japan"),
        ("cities", "Toronto Ontario", "Tokyo"),
        ("regions", "America", "Asia"),
    ],
)
async def test_orphaned_values_stop_upstream_update(
    field: str, valid: str, orphan: str
) -> None:
    async with browser_for() as (browser, requests):
        response = await browser.put("/api/selection", json={field: [valid, orphan]})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "incoherent_selection"
    assert not any(request.method == "PUT" for request in requests)


@pytest.mark.anyio
async def test_names_and_location_filters_reach_gluetun_together() -> None:
    selection = Selection(names=("Agena",), countries=("Canada",))
    async with browser_for() as (browser, requests):
        response = await browser.put("/api/selection", json=selection.to_dict())
    assert response.status_code == 200
    update = next(request for request in requests if request.method == "PUT")
    assert (
        json.loads(update.content)["provider"]["server_selection"]
        == selection.to_dict()
    )


@pytest.mark.anyio
async def test_known_value_without_support_stops_upstream_update() -> None:
    servers = [api_server(), api_server(public_name="Japan1", country_name="Japan")]
    async with browser_for(servers=servers) as (browser, requests):
        response = await browser.put(
            "/api/selection",
            json={"names": ["Agena"], "countries": ["Canada", "Japan"]},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "incoherent_selection"
    assert not any(request.method == "PUT" for request in requests)


@pytest.mark.anyio
async def test_stale_filters_leave_an_editable_draft() -> None:
    async with browser_for(selection=Selection(names=("Missing",))) as (
        browser,
        requests,
    ):
        response = await browser.get("/")
    assert 'data-can-change="true"' in response.text
    assert "<dd>Missing</dd>" in response.text
    assert "Deselected: Missing (names)." in response.text
    assert 'value="Missing"' not in response.text
    assert not any(request.method == "PUT" for request in requests)


@pytest.mark.parametrize(
    ("bandwidth", "maximum", "expected"),
    [
        (1250, 2000, "1 of 2 Gbps"),
        (1999, 2000, "1 of 2 Gbps"),
        (999, 2000, "0 of 2 Gbps"),
        (12.99, 100, "12 of 100 Mbps"),
        (1_999_999, 2_500_000, "1 of 2.5 Tbps"),
        (0, 0, "0 of 0 Mbps"),
    ],
)
def test_bandwidth_usage_floors_current_value_in_the_display_unit(
    bandwidth: float, maximum: float, expected: str
) -> None:
    assert _format_bandwidth_usage(bandwidth, maximum) == expected


def test_option_counts_use_other_filters_without_a_location_tree() -> None:
    original = parse_catalog({"servers": [api_server()]})[0]
    servers = (
        original,
        replace(original, name="Other", city="Montreal Quebec"),
        replace(original, country="Japan", region="Asia"),
    )
    options = _options(
        servers, Selection(countries=("Canada",), cities=("Toronto Ontario",))
    )
    countries = {option["value"]: option for option in options["countries"]}
    assert countries["Canada"]["server_count"] == 1
    assert countries["Canada"]["bandwidth"] == original.bandwidth
    assert countries["Japan"]["available"]
    assert all(option["available"] for option in options["cities"])
    names = {option["value"]: option for option in options["names"]}
    assert names["Agena"]["implied"]
    assert names["Agena"]["server_count"] == 1
    assert not names["Other"]["available"]
