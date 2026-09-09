# Gluetun AirVPN selector

Configure the AirVPN servers used by Gluetun via the
[Gluetun control server](https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md).

Limitation: Gluetun resets its server selection to the configured default after
a restart.

## Installation

Install the command from GitHub with uv:

```bash
uv tool install git+https://github.com/t-mart/gluetun-airvpn-selector
```

Alternatively, pull the
[Docker image](https://hub.docker.com/r/tmmrtn/gluetun-airvpn-selector):

```bash
docker pull tmmrtn/gluetun-airvpn-selector
```

For Docker Compose, merge this example into your existing Gluetun Compose file:

```yaml
services:
  gluetun:
    # Keep the existing Gluetun image, VPN settings, capabilities, and devices.
    ports:
      - "127.0.0.1:8081:8081"
    environment:
      HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: >-
        {"auth":"apikey","apikey":"your-api-key"}

  airvpn-selector:
    image: tmmrtn/gluetun-airvpn-selector
    network_mode: "service:gluetun"
    depends_on:
      - gluetun
    environment:
      GLUETUN_BASE_URL: http://127.0.0.1:8000
      HOST: 0.0.0.0
```

Set `GLUETUN_API_KEY` in the shell or the Compose `.env` file. Compose uses this
value only to configure Gluetun.

## Configuration

| Variable           | Default                 | Purpose                    |
| ------------------ | ----------------------- | -------------------------- |
| `GLUETUN_BASE_URL` | `http://127.0.0.1:8000` | Gluetun control server URL |
| `HOST`             | `127.0.0.1`             | bind address               |
| `PORT`             | `8081`                  | bind port                  |

A service restart invalidates all sessions.

## Development

Run all quality tasks:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
node --test tests/test_selection.mjs
```
