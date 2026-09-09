from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from math import floor
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx2
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import Config
from .domain import (
    SELECTOR_ATTRIBUTES,
    SELECTOR_FIELDS,
    Selection,
    SelectionError,
    canonicalize_selection,
    eligible_servers,
    normalize_selection,
    prune_selection,
)
from .services import (
    PUBLIC_IP_RETRY_SECONDS,
    AirVPNCatalog,
    Credentials,
    GluetunClient,
    GluetunState,
    ServiceError,
    SessionStore,
)

PACKAGE_DIR = Path(__file__).parent
SESSION_COOKIE = "gluetun_companion_session"
SESSION_MAX_AGE = 14 * 24 * 60 * 60
MAX_REQUEST_BYTES = 64 * 1024


def _bandwidth_unit(mbps: float) -> tuple[float, str]:
    if mbps >= 1_000_000:
        return 1_000_000, "Tbps"
    if mbps >= 1_000:
        return 1_000, "Gbps"
    return 1, "Mbps"


def _format_bandwidth_usage(bandwidth: float, bandwidth_max: float) -> str:
    scale, unit = _bandwidth_unit(bandwidth_max)
    percentage = _round_percentage(_utilization(bandwidth, bandwidth_max))
    maximum = _format_decimal(bandwidth_max / scale)
    return f"{percentage}% of {maximum} {unit}"


def _format_decimal(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _format_users(value: float) -> str:
    return f"{round(value):,}"


def _round_percentage(value: float) -> int:
    return floor(value + 0.5)


def _utilization(bandwidth: float, bandwidth_max: float) -> float:
    if bandwidth_max <= 0:
        return 0.0
    return min(100.0, max(0.0, bandwidth / bandwidth_max * 100))


def _utilization_color(value: float) -> str:
    if value >= 95:
        return "red"
    if value >= 75:
        return "yellow"
    return "green"


templates = Environment(
    loader=FileSystemLoader(PACKAGE_DIR / "templates"),
    autoescape=select_autoescape(("html", "xml")),
)
templates.filters.update(
    bandwidth_usage=_format_bandwidth_usage,
    percentage=_round_percentage,
    users=_format_users,
    utilization=_utilization,
    utilization_color=_utilization_color,
)


class SecurityHeadersMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    (
                        (
                            b"content-security-policy",
                            _content_security_policy().encode(),
                        ),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                    )
                )
                if not scope.get("path", "").startswith("/static/"):
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(
    config: Config | None = None,
    client: httpx2.AsyncClient | None = None,
) -> Starlette:
    app_config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        shared_client = client or httpx2.AsyncClient()
        app.state.config = app_config
        app.state.sessions = SessionStore()
        app.state.catalog = AirVPNCatalog(shared_client, app_config)
        app.state.gluetun = GluetunClient(shared_client, app_config)
        app.state.connectivity = {}
        try:
            yield
        finally:
            await shared_client.aclose()

    routes = [
        Route("/", homepage, methods=["GET"]),
        Route("/login", login_page, methods=["GET"]),
        Route("/login", login, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/api/state", api_state, methods=["GET"]),
        Route("/api/selection", api_selection, methods=["PUT"]),
        Route("/api/connectivity-test", connectivity_test, methods=["POST"]),
        Route("/healthz", healthz, methods=["GET"]),
        Mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static"),
    ]
    return Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[_middleware(SecurityHeadersMiddleware)],
    )


async def homepage(request: Request) -> Response:
    credentials = _request_credentials(request)

    state: GluetunState | None = None
    state_error: ServiceError | None = None
    connectivity_error: ServiceError | None = None
    try:
        state = await request.app.state.gluetun.get_state(credentials)
    except ServiceError as error:
        if error.status_code == 401:
            return _authentication_failed(request)
        state_error = error

    if state and request.query_params.get("skip_public_ip") != "1":
        try:
            result = await request.app.state.gluetun.connectivity_test(credentials)
            request.app.state.connectivity[_session_key(request)] = result
        except ServiceError as error:
            if error.status_code == 401:
                return _authentication_failed(request)
            connectivity_error = error

    snapshot = None
    catalog_error: ServiceError | None = None
    try:
        snapshot = await request.app.state.catalog.get(
            force=request.query_params.get("refresh") == "1"
        )
    except ServiceError as error:
        catalog_error = error

    context = _page_context(
        request,
        state,
        snapshot,
        state_error,
        catalog_error,
        connectivity_error,
    )
    return _template("index.html", context)


async def login_page(request: Request) -> Response:
    return _template("login.html", {"error": None})


async def login(request: Request) -> Response:
    try:
        body = await _limited_body(request)
    except ServiceError as error:
        return _error_response(error)
    form = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    supplied = Credentials(
        api_key=_first(form, "api_key"),
    )

    try:
        await request.app.state.gluetun.get_state(supplied)
    except ServiceError as error:
        return _template(
            "login.html",
            {
                "error": error.message,
            },
            status_code=error.status_code,
        )

    token = request.app.state.sessions.create(supplied)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return response


async def logout(request: Request) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    request.app.state.sessions.delete(token)
    request.app.state.connectivity.pop(token or "anonymous", None)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


async def api_state(request: Request) -> Response:
    credentials = _request_credentials(request)
    try:
        state, snapshot = await asyncio.gather(
            request.app.state.gluetun.get_state(credentials),
            request.app.state.catalog.get(),
        )
    except ServiceError as error:
        return _error_response(error)
    return JSONResponse(_state_document(state, snapshot))


async def api_selection(request: Request) -> Response:
    credentials = _request_credentials(request)
    try:
        body = await _limited_body(request)
        try:
            document = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ServiceError(
                "invalid_json", "The request body is not valid JSON.", 400
            ) from error
        try:
            requested = normalize_selection(document)
        except SelectionError as error:
            raise ServiceError("invalid_selection", str(error), 400) from error

        snapshot = await request.app.state.catalog.get()
        selection = canonicalize_selection(requested, snapshot.servers)
        if not eligible_servers(snapshot.servers, selection):
            raise ServiceError(
                "empty_selection",
                "The selected filters match no healthy AirVPN servers.",
                400,
            )
        if prune_selection(snapshot.servers, selection) != selection:
            raise ServiceError(
                "incoherent_selection",
                "Each selected value must match at least one server in the result.",
                400,
            )

        current = await request.app.state.gluetun.get_state(credentials)
        if current.provider.casefold() != "airvpn":
            raise ServiceError(
                "provider_not_airvpn",
                "Gluetun does not use the AirVPN provider.",
                409,
            )
        confirmed = await request.app.state.gluetun.apply_selection(
            selection, credentials
        )
        return JSONResponse(_state_document(confirmed, snapshot))
    except ServiceError as error:
        return _error_response(error)


async def connectivity_test(request: Request) -> Response:
    credentials = _request_credentials(request)
    session_key = _session_key(request)
    previous = request.app.state.connectivity.get(session_key)
    after_selection = request.query_params.get("after_selection") == "1"
    try:
        initial_delay = (
            PUBLIC_IP_RETRY_SECONDS if after_selection and previous is None else 0
        )
        result = await request.app.state.gluetun.connectivity_test_with_retry(
            credentials,
            initial_delay_seconds=initial_delay,
            previous_ip=previous.ip if after_selection and previous else None,
        )
    except ServiceError as error:
        return _error_response(error)
    request.app.state.connectivity[session_key] = result
    return JSONResponse(result.to_dict())


async def healthz(request: Request) -> Response:
    return JSONResponse({"status": "ok"})


def _page_context(
    request: Request,
    state: GluetunState | None,
    snapshot: Any,
    state_error: ServiceError | None,
    catalog_error: ServiceError | None,
    connectivity_error: ServiceError | None,
) -> dict[str, Any]:
    servers = snapshot.servers if snapshot else ()
    selection = canonicalize_selection(
        state.selection if state else _empty_selection(), servers
    )
    editor_selection = prune_selection(servers, selection)
    removed = [
        f"{value} ({field})"
        for field in SELECTOR_FIELDS
        for value in getattr(selection, field)
        if value not in getattr(editor_selection, field)
    ]
    can_change = bool(state and state.provider.casefold() == "airvpn" and servers)
    return {
        "state": state,
        "selection": selection,
        "servers": servers,
        "match_count": len(eligible_servers(servers, editor_selection)),
        "selection_notice": f"Deselected: {', '.join(removed)}." if removed else "",
        "catalog_rows": [
            {
                **{
                    attribute: getattr(server, attribute)
                    for attribute in SELECTOR_ATTRIBUTES.values()
                },
                "bandwidth": server.bandwidth,
                "bandwidthMax": server.bandwidth_max,
                "users": server.users,
            }
            for server in servers
        ],
        "options": _options(servers, editor_selection),
        "catalog_age": snapshot.age_seconds if snapshot else None,
        "catalog_error": snapshot.error if snapshot else _message(catalog_error),
        "state_error": _message(state_error),
        "connectivity_error": _message(connectivity_error),
        "can_change": can_change,
        "connectivity": request.app.state.connectivity.get(_session_key(request)),
    }


def _state_document(state: GluetunState, snapshot: Any) -> dict[str, Any]:
    eligible = eligible_servers(snapshot.servers, state.selection)
    return {
        "provider": state.provider,
        "vpn_status": state.vpn_status,
        "filters": state.selection.to_dict(),
        "catalog": {
            "age_seconds": snapshot.age_seconds,
            "error": snapshot.error,
            "server_count": len(snapshot.servers),
        },
        "eligible_servers": [server.to_dict() for server in eligible],
        "can_change": state.provider.casefold() == "airvpn",
    }


def _options(
    servers: tuple[Any, ...], selection: Any
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for field, attribute in SELECTOR_ATTRIBUTES.items():
        others = set(eligible_servers(servers, replace(selection, **{field: ()})))
        candidates = {getattr(server, attribute).casefold() for server in others}
        grouped: dict[str, dict[str, Any]] = {}
        for server in servers:
            value = getattr(server, attribute)
            key = value.casefold()
            option = grouped.setdefault(
                key,
                {
                    "value": value,
                    "bandwidth": 0.0,
                    "bandwidth_max": 0.0,
                    "users": 0,
                    "server_count": 0,
                    "flag": "",
                    "country_code": "",
                    "locations": set(),
                    "country_flags": set(),
                },
            )
            if server in others:
                option["bandwidth"] += server.bandwidth
                option["bandwidth_max"] += server.bandwidth_max
                option["users"] += server.users
                option["server_count"] += 1
            option["country_flags"].add((server.country_code, server.flag))
            location = _option_location(field, server)
            if location:
                option["locations"].add(location)

        selected = {value.casefold(): value for value in getattr(selection, field)}
        for key, option in grouped.items():
            option["selected"] = key in selected
            option["available"] = key in candidates
            option["implied"] = not selected and candidates == {key}
            option["location"] = " / ".join(
                sorted(option.pop("locations"), key=str.casefold)
            )
            country_flags = option.pop("country_flags")
            if field != "regions" and len(country_flags) == 1:
                option["country_code"], option["flag"] = next(iter(country_flags))
            option["utilization"] = _utilization(
                option["bandwidth"], option["bandwidth_max"]
            )
        result[field] = sorted(
            grouped.values(), key=lambda option: option["value"].casefold()
        )
    return result


def _option_location(field: str, server: Any) -> str:
    parts = {
        "countries": (server.region,),
        "cities": (server.country, server.region),
        "names": (server.city, server.country, server.region),
    }.get(field, ())
    return ", ".join(filter(None, parts))


def _request_credentials(request: Request) -> Credentials:
    credentials = request.app.state.sessions.get(request.cookies.get(SESSION_COOKIE))
    return credentials or Credentials(api_key="")


async def _limited_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                raise ServiceError(
                    "request_too_large", "The request body is too large.", 413
                )
        except ValueError as error:
            raise ServiceError(
                "invalid_content_length", "The Content-Length header is not valid.", 400
            ) from error
    body = await request.body()
    if len(body) > MAX_REQUEST_BYTES:
        raise ServiceError("request_too_large", "The request body is too large.", 413)
    return body


def _login_redirect(request: Request) -> Response:
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


def _authentication_failed(request: Request) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    request.app.state.sessions.delete(token)
    response = _login_redirect(request)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def _template(
    name: str, context: dict[str, Any], status_code: int = 200
) -> HTMLResponse:
    return HTMLResponse(templates.get_template(name).render(**context), status_code)


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status_code
    )


def _error_response(error: ServiceError) -> JSONResponse:
    return _error(error.code, error.message, error.status_code)


def _message(error: ServiceError | None) -> str | None:
    return error.message if error else None


def _first(form: dict[str, list[str]], name: str) -> str:
    values = form.get(name)
    return values[0] if values else ""


def _empty_selection() -> Selection:
    return Selection()


def _session_key(request: Request) -> str:
    return request.cookies.get(SESSION_COOKIE) or "anonymous"


def _content_security_policy() -> str:
    return (
        "default-src 'none'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; "
        "frame-ancestors 'none'"
    )


def _middleware(middleware_class: type[Any]) -> Any:
    from starlette.middleware import Middleware

    return Middleware(middleware_class)
