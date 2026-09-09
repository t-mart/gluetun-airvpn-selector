from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from gluetun_airvpn_selector import services
from gluetun_airvpn_selector.config import Config
from gluetun_airvpn_selector.domain import Selection
from gluetun_airvpn_selector.services import (
    AirVPNCatalog,
    Credentials,
    GluetunClient,
    ServiceError,
    configured_credentials,
)
from tests.test_domain import api_server


def config(**overrides: Any) -> Config:
    values: dict[str, Any] = {
        "gluetun_base_url": "http://gluetun:8000",
        "gluetun_api_key": "secret",
        "airvpn_status_url": "https://airvpn.test/status",
        "airvpn_cache_seconds": 300,
        "airvpn_timeout_seconds": 5,
        "gluetun_timeout_seconds": 5,
        "host": "127.0.0.1",
        "port": 8081,
    }
    values.update(overrides)
    return Config(**values)


@pytest.mark.anyio
async def test_apply_sends_all_arrays_and_reads_confirmed_state() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/v1/vpn/status":
            return httpx2.Response(200, json={"status": "running"})
        if request.method == "PUT":
            return httpx2.Response(200, text="VPN settings changed")
        return httpx2.Response(
            200,
            json={
                "provider": {
                    "name": "airvpn",
                    "server_selection": {
                        "names": ["Agena"],
                        "countries": [],
                        "cities": [],
                        "regions": [],
                    },
                }
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        gluetun = GluetunClient(client, config())
        state = await gluetun.apply_selection(
            Selection(names=("Agena",)), Credentials(api_key="secret")
        )

    put_request = next(request for request in requests if request.method == "PUT")
    assert json.loads(put_request.content) == {
        "provider": {
            "server_selection": {
                "names": ["Agena"],
                "countries": [],
                "cities": [],
                "regions": [],
            }
        }
    }
    assert state.selection.names == ("Agena",)


@pytest.mark.anyio
async def test_state_treats_null_server_filters_as_empty() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/vpn/status":
            return httpx2.Response(200, json={"status": "running"})
        return httpx2.Response(
            200,
            json={
                "provider": {
                    "name": "airvpn",
                    "server_selection": {
                        "names": None,
                        "countries": None,
                        "cities": ["Denver Colorado"],
                        "regions": None,
                    },
                }
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        state = await GluetunClient(client, config()).get_state(
            Credentials(api_key="secret")
        )

    assert state.selection == Selection(cities=("Denver Colorado",))


@pytest.mark.anyio
async def test_authentication_header() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.path.endswith("status"):
            return httpx2.Response(200, json={"status": "running"})
        return httpx2.Response(
            200,
            json={"provider": {"name": "airvpn", "server_selection": {}}},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        await GluetunClient(client, config()).get_state(Credentials(api_key="secret"))

    values = [request.headers.get("X-API-Key") for request in seen]
    assert values == ["secret", "secret"]


def test_config_requires_api_key() -> None:
    with pytest.raises(ValueError, match="GLUETUN_API_KEY"):
        config(gluetun_api_key="").validate()


def test_config_accepts_api_key() -> None:
    config().validate()


def test_configured_credentials_uses_api_key() -> None:
    credentials = configured_credentials(config())

    assert credentials == Credentials(api_key="secret")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "code"),
    [(400, "gluetun_rejected_selection"), (401, "gluetun_authentication_error")],
)
async def test_gluetun_http_errors(status_code: int, code: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, text="upstream detail")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(ServiceError) as caught:
            await GluetunClient(client, config()).get_state(
                Credentials(api_key="secret")
            )

    assert caught.value.code == code


@pytest.mark.anyio
async def test_gluetun_timeout() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("late", request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(ServiceError, match="timed out"):
            await GluetunClient(client, config()).get_state(
                Credentials(api_key="secret")
            )


@pytest.mark.anyio
async def test_gluetun_connection_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(ServiceError, match="connect"):
            await GluetunClient(client, config()).get_state(
                Credentials(api_key="secret")
            )


@pytest.mark.anyio
async def test_catalog_keeps_last_success_after_refresh_error() -> None:
    responses = iter(
        [
            httpx2.Response(200, json={"servers": [api_server()]}),
            httpx2.Response(503),
        ]
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        return next(responses)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        catalog = AirVPNCatalog(client, config())
        first = await catalog.get()
        stale = await catalog.get(force=True)

    assert stale.servers == first.servers
    assert stale.fetched_at == first.fetched_at
    assert stale.error == "The AirVPN service returned HTTP 503."


@pytest.mark.anyio
async def test_catalog_rejects_invalid_json_without_cache() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"not json")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(ServiceError, match="not valid JSON"):
            await AirVPNCatalog(client, config()).get()


@pytest.mark.anyio
async def test_public_ip_response_validation() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"public_ip": "not-an-ip"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(ServiceError) as caught:
            await GluetunClient(client, config()).connectivity_test(
                Credentials(api_key="secret")
            )

    assert caught.value.code == "invalid_public_ip"


@pytest.mark.anyio
async def test_public_ip_retries_after_reconnect_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(("not-an-ip", "203.0.113.8"))
    delays: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"public_ip": next(responses)})

    async def record_delay(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(services.asyncio, "sleep", record_delay)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        result = await GluetunClient(client, config()).connectivity_test_with_retry(
            Credentials(api_key="secret"), initial_delay_seconds=5
        )

    assert result.ip == "203.0.113.8"
    assert delays == [5, 5]
