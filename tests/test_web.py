from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from starlette.applications import Starlette

from gluetun_airvpn_selector.web import create_app
from tests.test_domain import api_server
from tests.test_services import config


def test_app_requires_api_key() -> None:
    with pytest.raises(ValueError, match="GLUETUN_API_KEY"):
        create_app(config(gluetun_api_key=""))


@asynccontextmanager
async def browser_for(
    *, api_key: str = "secret", gluetun_status: int = 200, authenticated: bool = True
) -> AsyncIterator[tuple[httpx2.AsyncClient, list[httpx2.Request]]]:
    requests: list[httpx2.Request] = []

    def upstream(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.host == "airvpn.test":
            return httpx2.Response(200, json={"servers": [api_server()]})
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
                    "server_selection": {
                        "names": [],
                        "countries": ["Canada"],
                        "cities": [],
                        "regions": [],
                    },
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
    assert "Configured filters" in response.text
    assert "SERVER_COUNTRIES" in response.text
    assert "Agena" in response.text
    assert "203.0.113.8" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]


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
