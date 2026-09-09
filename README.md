# Gluetun AirVPN selector

This companion website controls AirVPN server filters through the Gluetun control server.

It reads the live AirVPN catalog. It does not persist Gluetun changes after a Gluetun restart.

## Run the service

1. Configure API key authentication on the Gluetun control server.
2. Set the same API key for this service.
3. Run `uv run gluetun-airvpn-selector`.
4. Open `http://127.0.0.1:8081`.

The service binds to `127.0.0.1` by default.

Set `HOST=0.0.0.0` only inside a trusted network or an authenticated proxy.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `GLUETUN_BASE_URL` | `http://127.0.0.1:8000` | Gluetun control server URL |
| `GLUETUN_API_KEY` | Required | Gluetun API key |
| `AIRVPN_STATUS_URL` | `https://airvpn.org/api/status/` | AirVPN catalog URL |
| `AIRVPN_CACHE_SECONDS` | `300` | Successful catalog cache duration |
| `AIRVPN_TIMEOUT_SECONDS` | `15` | AirVPN request timeout |
| `GLUETUN_TIMEOUT_SECONDS` | `10` | Gluetun request timeout |
| `HOST` | `127.0.0.1` | Companion bind address |
| `PORT` | `8081` | Companion bind port |

The service requires a login. The cookie contains an opaque session token.

The service keeps the Gluetun API key in process memory. A service restart invalidates all sessions.

## Development

Run all quality tasks:

```text
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
node --test tests/test_selection.mjs
```

Set `RUN_LIVE_AIRVPN_TEST=1` to enable the optional AirVPN contract test.
