# Gluetun AirVPN selector

Configure the AirVPN servers used by Gluetun via the
[Gluetun control server](https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md).

Limitations:

- Gluetun will revert to its default configured servers after restart
- Only API key control server authentication is supported

## Installation

Install the command from GitHub with uv:

```bash
uv tool install git+https://github.com/t-mart/gluetun-airvpn-selector
```

Alternatively, pull the [Docker image](https://hub.docker.com/r/tmmrtn/gluetun-airvpn-selector):

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
        {"auth":"apikey","apikey":"${GLUETUN_API_KEY:?Set GLUETUN_API_KEY}"}

  airvpn-selector:
    image: tmmrtn/gluetun-airvpn-selector
    network_mode: "service:gluetun"
    depends_on:
      - gluetun
    environment:
      GLUETUN_BASE_URL: http://127.0.0.1:8000
      GLUETUN_API_KEY: "${GLUETUN_API_KEY:?Set GLUETUN_API_KEY}"
      HOST: 0.0.0.0
```

Set `GLUETUN_API_KEY` in the shell or the Compose `.env` file. Use the same key for both services.

Start the Compose services:

```bash
docker compose up --detach
```

## Run the uv installation

1. Configure API key authentication on the Gluetun control server.
2. Set the same API key for this service.
3. Run `gluetun-airvpn-selector`.
4. Open `http://127.0.0.1:8081`.

The service binds to `127.0.0.1` by default.

Set `HOST=0.0.0.0` only inside a trusted network or an authenticated proxy.

## Configuration

| Variable           | Default                 | Purpose                    |
| ------------------ | ----------------------- | -------------------------- |
| `GLUETUN_BASE_URL` | `http://127.0.0.1:8000` | Gluetun control server URL |
| `GLUETUN_API_KEY`  | Required                | Gluetun API key            |
| `HOST`             | `127.0.0.1`             | Companion bind address     |
| `PORT`             | `8081`                  | Companion bind port        |

The service keeps the Gluetun API key in process memory. A service restart
invalidates all sessions.

## Development

Run all quality tasks:

```bash
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
node --test tests/test_selection.mjs
```
