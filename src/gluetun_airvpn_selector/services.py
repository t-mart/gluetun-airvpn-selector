from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Any

import httpx2

from .config import Config
from .domain import (
    SELECTOR_FIELDS,
    CatalogError,
    Selection,
    Server,
    parse_catalog,
)

IP_TIMEOUT_SECONDS = 10.0
PUBLIC_IP_RETRY_ATTEMPTS = 3
PUBLIC_IP_RETRY_SECONDS = 5.0


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Credentials:
    api_key: str


@dataclass(frozen=True, slots=True)
class GluetunState:
    provider: str
    vpn_status: str
    selection: Selection


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    servers: tuple[Server, ...]
    fetched_at: datetime
    error: str | None

    @property
    def age_seconds(self) -> int:
        return max(0, int((datetime.now(UTC) - self.fetched_at).total_seconds()))


@dataclass(frozen=True, slots=True)
class ConnectivityResult:
    ip: str
    duration_ms: int
    source_url: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip": self.ip,
            "duration_ms": self.duration_ms,
            "source_url": self.source_url,
            "observed_at": self.observed_at,
        }


class SessionStore:
    def __init__(self, lifetime: timedelta = timedelta(days=14)) -> None:
        self._lifetime = lifetime
        self._sessions: dict[str, tuple[datetime, Credentials]] = {}

    def create(self, credentials: Credentials) -> str:
        self._remove_expired()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = (datetime.now(UTC) + self._lifetime, credentials)
        return token

    def get(self, token: str | None) -> Credentials | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        expires_at, credentials = session
        if expires_at <= datetime.now(UTC):
            self._sessions.pop(token, None)
            return None
        return credentials

    def delete(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def _remove_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [
            token for token, (expires, _) in self._sessions.items() if expires <= now
        ]
        for token in expired:
            self._sessions.pop(token, None)


class AirVPNCatalog:
    def __init__(self, client: httpx2.AsyncClient, config: Config) -> None:
        self._client = client
        self._url = config.airvpn_status_url
        self._cache_seconds = config.airvpn_cache_seconds
        self._timeout = config.airvpn_timeout_seconds
        self._snapshot: CatalogSnapshot | None = None
        self._last_attempt = 0.0
        self._lock = asyncio.Lock()

    async def get(self, *, force: bool = False) -> CatalogSnapshot:
        if not force and self._snapshot_is_fresh():
            assert self._snapshot is not None
            return self._snapshot

        async with self._lock:
            if not force and self._snapshot_is_fresh():
                assert self._snapshot is not None
                return self._snapshot
            self._last_attempt = time.monotonic()
            try:
                response = await self._client.get(self._url, timeout=self._timeout)
                if response.status_code != 200:
                    raise CatalogError(
                        f"The AirVPN service returned HTTP {response.status_code}."
                    )
                servers = parse_catalog(response.json())
                if not servers:
                    raise CatalogError(
                        "The AirVPN catalog contains no healthy servers."
                    )
                self._snapshot = CatalogSnapshot(
                    servers=servers,
                    fetched_at=datetime.now(UTC),
                    error=None,
                )
            except (
                CatalogError,
                ValueError,
                httpx2.HTTPError,
            ) as error:
                message = _catalog_error_message(error)
                if self._snapshot is None:
                    raise ServiceError("airvpn_unavailable", message) from error
                self._snapshot = CatalogSnapshot(
                    servers=self._snapshot.servers,
                    fetched_at=self._snapshot.fetched_at,
                    error=message,
                )
            return self._snapshot

    def _snapshot_is_fresh(self) -> bool:
        if self._snapshot is None:
            return False
        if self._snapshot.error:
            return time.monotonic() - self._last_attempt < self._cache_seconds
        return self._snapshot.age_seconds < self._cache_seconds


class GluetunClient:
    def __init__(self, client: httpx2.AsyncClient, config: Config) -> None:
        self._client = client
        self._base_url = config.gluetun_base_url
        self._timeout = config.gluetun_timeout_seconds
        self._change_lock = asyncio.Lock()

    async def get_state(self, credentials: Credentials) -> GluetunState:
        settings_response, status_response = await asyncio.gather(
            self._request("GET", "/v1/vpn/settings", credentials),
            self._request("GET", "/v1/vpn/status", credentials),
        )
        settings = _json_object(settings_response, "Gluetun settings")
        status = _json_object(status_response, "Gluetun VPN status")
        provider = settings.get("provider")
        if not isinstance(provider, dict) or not isinstance(provider.get("name"), str):
            raise ServiceError(
                "invalid_gluetun_response",
                "The Gluetun settings response has no provider name.",
            )
        status_value = status.get("status")
        if not isinstance(status_value, str):
            raise ServiceError(
                "invalid_gluetun_response",
                "The Gluetun VPN status response has no status value.",
            )
        return GluetunState(
            provider=provider["name"],
            vpn_status=status_value,
            selection=_selection_from_provider(provider),
        )

    async def apply_selection(
        self,
        selection: Selection,
        credentials: Credentials,
    ) -> GluetunState:
        payload = {"provider": {"server_selection": selection.to_dict()}}
        async with self._change_lock:
            await self._request("PUT", "/v1/vpn/settings", credentials, json=payload)
            return await self.get_state(credentials)

    async def connectivity_test(self, credentials: Credentials) -> ConnectivityResult:
        source_url = f"{self._base_url}/v1/publicip/ip"
        started = time.monotonic()
        response = await self._request(
            "GET",
            "/v1/publicip/ip",
            credentials,
            timeout=IP_TIMEOUT_SECONDS,
        )
        duration_ms = round((time.monotonic() - started) * 1000)
        document = _json_object(response, "Gluetun public IP")
        value = document.get("public_ip")
        if not isinstance(value, str):
            raise ServiceError(
                "invalid_public_ip",
                "The Gluetun public IP response has no public_ip value.",
            )
        try:
            parsed = ip_address(value)
        except ValueError as error:
            raise ServiceError(
                "invalid_public_ip",
                "The Gluetun public IP response contains an invalid IP address.",
            ) from error
        return ConnectivityResult(
            ip=str(parsed),
            duration_ms=duration_ms,
            source_url=source_url,
            observed_at=datetime.now(UTC).isoformat(),
        )

    async def connectivity_test_with_retry(
        self,
        credentials: Credentials,
        *,
        initial_delay_seconds: float = 0,
    ) -> ConnectivityResult:
        if initial_delay_seconds > 0:
            await asyncio.sleep(initial_delay_seconds)

        attempt = 1
        while True:
            try:
                return await self.connectivity_test(credentials)
            except ServiceError as error:
                if error.status_code == 401 or attempt >= PUBLIC_IP_RETRY_ATTEMPTS:
                    raise
                attempt += 1
                await asyncio.sleep(PUBLIC_IP_RETRY_SECONDS)

    async def _request(
        self,
        method: str,
        path: str,
        credentials: Credentials,
        *,
        json: Any = None,
        timeout: float | None = None,
    ) -> httpx2.Response:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers={"X-API-Key": credentials.api_key},
                json=json,
                timeout=timeout or self._timeout,
            )
        except httpx2.TimeoutException as error:
            raise ServiceError(
                "gluetun_timeout", "The Gluetun request timed out.", 504
            ) from error
        except httpx2.ConnectError as error:
            raise ServiceError(
                "gluetun_connection_error",
                "The companion cannot connect to Gluetun.",
                502,
            ) from error
        except httpx2.HTTPError as error:
            raise ServiceError(
                "gluetun_request_error", "The Gluetun request failed.", 502
            ) from error

        if response.status_code == 401:
            raise ServiceError(
                "gluetun_authentication_error",
                "Gluetun rejected the API key.",
                401,
            )
        if response.status_code == 400:
            message = response.text.strip()[:500] or "Gluetun rejected the selection."
            raise ServiceError("gluetun_rejected_selection", message, 400)
        if response.status_code < 200 or response.status_code >= 300:
            raise ServiceError(
                "gluetun_http_error",
                f"Gluetun returned HTTP {response.status_code}.",
                502,
            )
        return response


def configured_credentials(config: Config) -> Credentials:
    return Credentials(api_key=config.gluetun_api_key)


def credentials_match(expected: Credentials, supplied: Credentials) -> bool:
    return secrets.compare_digest(expected.api_key, supplied.api_key)


def _selection_from_provider(provider: dict[str, Any]) -> Selection:
    source = provider.get("server_selection", {})
    if not isinstance(source, dict):
        raise ServiceError(
            "invalid_gluetun_response",
            "The Gluetun server_selection value must be an object.",
        )
    values: dict[str, tuple[str, ...]] = {}
    for field in SELECTOR_FIELDS:
        value = source.get(field)
        if value is None:
            values[field] = ()
            continue
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ServiceError(
                "invalid_gluetun_response",
                f"The Gluetun {field} value must be a string array.",
            )
        values[field] = tuple(value)
    return Selection(**values)


def _json_object(response: httpx2.Response, source: str) -> dict[str, Any]:
    try:
        document = response.json()
    except ValueError as error:
        raise ServiceError(
            "invalid_gluetun_response", f"The {source} response is not valid JSON."
        ) from error
    if not isinstance(document, dict):
        raise ServiceError(
            "invalid_gluetun_response", f"The {source} response must be an object."
        )
    return document


def _catalog_error_message(error: Exception) -> str:
    if isinstance(error, httpx2.TimeoutException):
        return "The AirVPN catalog request timed out."
    if isinstance(error, httpx2.HTTPError):
        return "The AirVPN catalog request failed."
    if isinstance(error, ValueError) and not isinstance(error, CatalogError):
        return "The AirVPN response is not valid JSON."
    return str(error)
