import os

import httpx2
import pytest

from gluetun_airvpn_selector.domain import parse_catalog


@pytest.mark.skipif(
    os.getenv("RUN_LIVE_AIRVPN_TEST") != "1",
    reason="Set RUN_LIVE_AIRVPN_TEST=1 to use the live AirVPN API.",
)
def test_live_airvpn_contract() -> None:
    response = httpx2.get("https://airvpn.org/api/status/", timeout=20)

    assert response.status_code == 200
    assert parse_catalog(response.json())
