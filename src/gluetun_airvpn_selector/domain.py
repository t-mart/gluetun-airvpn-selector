from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any

SELECTOR_FIELDS = ("names", "countries", "cities", "regions")
SELECTOR_ATTRIBUTES = dict(zip(SELECTOR_FIELDS, ("name", "country", "city", "region")))


class CatalogError(ValueError):
    pass


class SelectionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Server:
    name: str
    country: str
    country_code: str
    city: str
    region: str
    bandwidth: float
    bandwidth_max: float
    users: float
    load: float
    entry_addresses: tuple[str, ...]

    @property
    def flag(self) -> str:
        return country_flag(self.country_code)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["flag"] = self.flag
        return data


@dataclass(frozen=True, slots=True)
class Selection:
    names: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, list[str]]:
        return {field: list(getattr(self, field)) for field in SELECTOR_FIELDS}


def parse_catalog(document: Any) -> tuple[Server, ...]:
    if not isinstance(document, dict):
        raise CatalogError("The AirVPN document must be a JSON object.")

    raw_servers = document.get("servers")
    if not isinstance(raw_servers, list):
        raise CatalogError("The AirVPN document must contain a servers array.")

    servers: dict[str, Server] = {}
    for index, raw_server in enumerate(raw_servers):
        server = _parse_server(raw_server, index)
        if server is None:
            continue
        servers.setdefault(server.name.casefold(), server)

    return tuple(sorted(servers.values(), key=lambda server: server.name.casefold()))


def _parse_server(raw_server: Any, index: int) -> Server | None:
    path = f"servers[{index}]"
    if not isinstance(raw_server, dict):
        raise CatalogError(f"{path} must be an object.")

    name = _required_string(raw_server, "public_name", path)
    country = _required_string(raw_server, "country_name", path)
    country_code = _required_string(raw_server, "country_code", path)
    location = _required_string(raw_server, "location", path)
    region = _required_string(raw_server, "continent", path)
    health = _required_string(raw_server, "health", path)
    bandwidth = _required_number(raw_server, "bw", path)
    bandwidth_max = _required_number(raw_server, "bw_max", path)
    users_value = _required_number(raw_server, "users", path)
    load = _required_number(raw_server, "currentload", path)

    addresses: list[str] = []
    for family, address_type in (("ip_v4", IPv4Address), ("ip_v6", IPv6Address)):
        for number in range(1, 5):
            field = f"{family}_in{number}"
            value = _required_string(raw_server, field, path)
            try:
                address = ip_address(value)
            except ValueError as error:
                raise CatalogError(
                    f"{path}.{field} must be a valid IP address."
                ) from error
            if not isinstance(address, address_type):
                raise CatalogError(f"{path}.{field} has the wrong address family.")
            addresses.append(value)

    if health != "ok":
        return None

    city = location.replace(", ", " ").replace(",", "")
    return Server(
        name=name,
        country=country,
        country_code=country_code,
        city=city,
        region=region,
        bandwidth=bandwidth,
        bandwidth_max=bandwidth_max,
        users=users_value,
        load=load,
        entry_addresses=tuple(addresses),
    )


def _required_string(source: Mapping[str, Any], field: str, path: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{path}.{field} must be a non-empty string.")
    return value


def _required_number(source: Mapping[str, Any], field: str, path: str) -> float:
    value = source.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogError(f"{path}.{field} must be a number.")
    return float(value)


def normalize_selection(
    value: Any,
    *,
    max_values: int = 1024,
    max_value_length: int = 200,
) -> Selection:
    if not isinstance(value, dict):
        raise SelectionError("The request body must be a JSON object.")

    unknown = set(value) - set(SELECTOR_FIELDS)
    if unknown:
        raise SelectionError("The request body contains unknown selector fields.")

    normalized: dict[str, tuple[str, ...]] = {}
    for field in SELECTOR_FIELDS:
        items = value.get(field, [])
        if not isinstance(items, list):
            raise SelectionError(f"{field} must be an array.")
        if len(items) > max_values:
            raise SelectionError(f"{field} contains too many values.")

        unique: dict[str, str] = {}
        for item in items:
            if not isinstance(item, str) or not item.strip():
                raise SelectionError(f"{field} must contain non-empty strings.")
            clean = item.strip()
            if len(clean) > max_value_length:
                raise SelectionError(f"A {field} value is too long.")
            unique.setdefault(clean.casefold(), clean)
        normalized[field] = tuple(unique.values())

    return Selection(**normalized)


def eligible_servers(
    servers: Iterable[Server], selection: Selection
) -> tuple[Server, ...]:
    filters = {
        field: {value.casefold() for value in getattr(selection, field)}
        for field in SELECTOR_FIELDS
    }
    return tuple(
        server
        for server in servers
        if _matches(server.name, filters["names"])
        and _matches(server.country, filters["countries"])
        and _matches(server.city, filters["cities"])
        and _matches(server.region, filters["regions"])
    )


def _matches(value: str, choices: set[str]) -> bool:
    return not choices or value.casefold() in choices


def prune_selection(servers: Iterable[Server], selection: Selection) -> Selection:
    server_list = tuple(servers)
    while True:
        matches = eligible_servers(server_list, selection)
        supported = {
            field: {getattr(server, attribute).casefold() for server in matches}
            for field, attribute in SELECTOR_ATTRIBUTES.items()
        }
        pruned = Selection(
            **{
                field: tuple(
                    value
                    for value in getattr(selection, field)
                    if value.casefold() in supported[field]
                )
                for field in SELECTOR_FIELDS
            }
        )
        if pruned == selection:
            return pruned
        selection = pruned


def canonicalize_selection(
    selection: Selection, servers: Iterable[Server]
) -> Selection:
    server_list = tuple(servers)
    values = {
        "names": _canonical_values(
            selection.names, (server.name for server in server_list)
        ),
        "countries": _canonical_values(
            selection.countries, (server.country for server in server_list)
        ),
        "cities": _canonical_values(
            selection.cities, (server.city for server in server_list)
        ),
        "regions": _canonical_values(
            selection.regions, (server.region for server in server_list)
        ),
    }
    return Selection(**values)


def _canonical_values(
    selected: Iterable[str], available: Iterable[str]
) -> tuple[str, ...]:
    canonical = {value.casefold(): value for value in available}
    return tuple(canonical.get(value.casefold(), value) for value in selected)


def country_flag(country_code: str) -> str:
    code = country_code.strip().upper()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in code)
