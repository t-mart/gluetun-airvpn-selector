from copy import deepcopy

import pytest

from gluetun_airvpn_selector.domain import (
    CatalogError,
    Selection,
    eligible_servers,
    normalize_selection,
    parse_catalog,
)


def api_server(**overrides: object) -> dict[str, object]:
    server: dict[str, object] = {
        "public_name": "Agena",
        "country_name": "Canada",
        "country_code": "ca",
        "location": "Toronto, Ontario",
        "continent": "America",
        "bw": 1714,
        "bw_max": 2000,
        "users": 169,
        "currentload": 85,
        "health": "ok",
        "ip_v4_in1": "192.0.2.1",
        "ip_v4_in2": "192.0.2.2",
        "ip_v4_in3": "192.0.2.3",
        "ip_v4_in4": "192.0.2.4",
        "ip_v6_in1": "2001:db8::1",
        "ip_v6_in2": "2001:db8::2",
        "ip_v6_in3": "2001:db8::3",
        "ip_v6_in4": "2001:db8::4",
    }
    server.update(overrides)
    return server


def test_parser_keeps_healthy_servers_and_converts_city() -> None:
    servers = parse_catalog({"servers": [api_server()]})

    assert len(servers) == 1
    assert servers[0].city == "Toronto Ontario"


@pytest.mark.parametrize("health", ["warning", "error", "OK"])
def test_parser_ignores_non_ok_health(health: str) -> None:
    assert parse_catalog({"servers": [api_server(health=health)]}) == ()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"servers": "bad"}, "servers array"),
        ({"servers": [api_server(country_name=4)]}, "country_name"),
        ({"servers": [api_server(ip_v4_in1="bad")]}, "valid IP address"),
        ({"servers": [api_server(ip_v6_in1="192.0.2.8")]}, "wrong address family"),
    ],
)
def test_parser_rejects_invalid_documents(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(CatalogError, match=message):
        parse_catalog(deepcopy(change))


def test_multiple_values_use_or_within_one_field() -> None:
    servers = parse_catalog(
        {
            "servers": [
                api_server(),
                api_server(
                    public_name="Aladfar",
                    location="Montreal, Quebec",
                    ip_v4_in1="198.51.100.1",
                ),
            ]
        }
    )

    eligible = eligible_servers(
        servers, Selection(cities=("Toronto Ontario", "Montreal Quebec"))
    )

    assert {server.name for server in eligible} == {"Agena", "Aladfar"}


def test_multiple_fields_use_and() -> None:
    servers = parse_catalog({"servers": [api_server()]})

    eligible = eligible_servers(
        servers,
        Selection(countries=("Canada",), cities=("Denver Colorado",)),
    )

    assert eligible == ()


def test_comparisons_ignore_case() -> None:
    servers = parse_catalog({"servers": [api_server()]})

    assert eligible_servers(
        servers,
        Selection(names=("AGENA",), countries=("canada",), regions=("america",)),
    )


def test_selection_removes_case_insensitive_duplicates() -> None:
    selection = normalize_selection(
        {"names": [" Agena ", "agena"], "countries": [], "cities": [], "regions": []}
    )

    assert selection.names == ("Agena",)
