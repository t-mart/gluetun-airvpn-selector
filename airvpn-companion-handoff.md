# AirVPN Gluetun Companion Handoff

## Purpose

Build a small website that controls AirVPN server filters in a Gluetun instance.

The website must:

- Read the live AirVPN server catalog.
- Parse current gluetun settings and show the current servers as selected
- show unselected servers (and let them be selected)
- Support gluetun API key as authentication.

The service does not persist server changes. Gluetun restores its startup
configuration after a restart.

## Recommended stack

Use Python, Starlette, [HTTPX2](https://pydantic.dev/docs/httpx2/) (successor to
HTTPX), Uvicorn, and Jinja2.

Use server-rendered HTML with small JavaScript requests. Do not expose Gluetun
credentials to the browser.

Create one shared `httpx.AsyncClient` during the Starlette lifespan. Close the
client during shutdown.

## Required configuration

Read these environment variables:

| Variable            | Default                          | Purpose                          |
| ------------------- | -------------------------------- | -------------------------------- |
| `GLUETUN_BASE_URL`  | `http://127.0.0.1:8000`          | Gluetun control server URL       |
| `GLUETUN_API_KEY`   | required                         | Value for the `X-API-Key` header |
| `AIRVPN_STATUS_URL` | `https://airvpn.org/api/status/` | AirVPN catalog URL               |

Require `GLUETUN_API_KEY`. Send it through `X-API-Key`.

Never include credentials in logs or browser responses.

## Gluetun server semantics

Gluetun accepts multiple values for names, countries, cities, and regions.

Startup environment variables use comma-separated values:

```text
SERVER_CITIES=Denver,Chicago
SERVER_COUNTRIES=United States,Canada
SERVER_NAMES=Aladfar,Agena
SERVER_REGIONS=America,Europe
```

The startup parser trims spaces and quotes. It also converts values to
lowercase.

The control API uses JSON arrays. Its comparisons ignore letter case.

Gluetun uses logical OR within each non-empty field.

```text
cities = [Denver, Chicago]
```

This value selects servers in Denver or Chicago.

Gluetun uses logical AND between different non-empty fields.

```text
cities = [Denver]
countries = [Canada]
```

This value requires a server in Denver and Canada. The current AirVPN catalog
has no such server.

Formally, a server is eligible when all these expressions are true:

```text
names is empty OR server.name is in names
AND countries is empty OR server.country is in countries
AND cities is empty OR server.city is in cities
AND regions is empty OR server.region is in regions
```

Gluetun validates each field separately. It does not validate the final
intersection.

Reject an empty intersection in the companion before the API request. This
prevents a failed VPN restart.

## AirVPN catalog parser

Fetch the [AirVPN status document](https://airvpn.org/api/status/) with an HTTP
GET request.

see

- airvpn-api-status.json (example response)
- airvpn-api-status.schema.json for more deets

Require an HTTP `200` response. Require a JSON object with a `servers` array.

Ignore unknown fields. Fail the refresh when the document or a required field
has an invalid type.

Gluetun reads these fields from each server:

| AirVPN field                    | Companion field | Rule                           |
| ------------------------------- | --------------- | ------------------------------ |
| `public_name`                   | `name`          | Preserve the value             |
| `country_name`                  | `country`       | Preserve the value             |
| `country_code`                  | `country_code`  | Preserve the value             |
| `location`                      | `city`          | Replace each `", "` with `" "` |
| `continent`                     | `region`        | Preserve the value             |
| `health`                        | `health`        | Keep only the exact value `ok` |
| `ip_v4_in1` through `ip_v4_in4` | entry addresses | Validate as IPv4 addresses     |
| `ip_v6_in1` through `ip_v6_in4` | entry addresses | Validate as IPv6 addresses     |

```go
    	city := strings.ReplaceAll(apiServer.Location, ", ", " ")
    	city = strings.ReplaceAll(city, ",", "") |
```

For example, Gluetun converts `Toronto, Ontario` to `Toronto Ontario`.

Gluetun ignores `bw`, `bw_max`, `users`, and `currentload` during server
selection, but these should be shown in the UI

The companion can retain these fields for display. Do not use them for Gluetun
filter emulation.

Gluetun creates several transport records for one physical AirVPN server.
Collapse website rows by `public_name`.

Use canonical values from the AirVPN document in control API requests. Use
case-insensitive comparisons for previews.

Cache the last successful catalog for `AIRVPN_CACHE_SECONDS`. Keep that catalog
when a later refresh fails.

Show the catalog age and refresh error. Do not replace the catalog with an empty
list after an error.

## Gluetun control API

### Read the current filters

Send this request:

```http
GET /v1/vpn/settings
```

Require `provider.name` to equal `airvpn`. Disable changes when Gluetun uses
another provider.

Read these response fields:

```text
provider.server_selection.names
provider.server_selection.countries
provider.server_selection.cities
provider.server_selection.regions
```

Treat absent fields and null values as empty arrays. Preserve unknown current
values and mark them as unavailable.

### Apply filters

Send this request:

```http
PUT /v1/vpn/settings
Content-Type: application/json

{
  "provider": {
    "server_selection": {
      "names": ["Agena"],
      "countries": [],
      "cities": [],
      "regions": []
    }
  }
}
```

Always send all four arrays. Empty arrays clear old filters from another
hierarchy.

Omitted fields remain unchanged because Gluetun treats this request as a patch.

Gluetun validates the result and restarts the VPN when settings differ. The
response body contains a plain-text outcome.

After the PUT request, fetch `/v1/vpn/settings` again. Display only the returned
state as the current state.

Serialize filter changes with one process-local lock. This prevents concurrent
VPN restarts from this service.

Propagate useful Gluetun errors without credentials. Distinguish `400`, `401`,
connection errors, and timeouts.

## Website behavior

Show these sections on one page:

1. Gluetun connection state and provider.
2. Current name, country, city, and region filters.
3. Eligible AirVPN servers from the live catalog.
4. A selection editor with multi-select controls.
5. The last public IP test result.

Use these labels:

- `Configured filters` for values from Gluetun.
- `Eligible servers` for the computed AirVPN subset.
- `Public IP` for the connectivity result.

Do not label one server as the active server. The Gluetun control API does not
expose that value.

If one eligible server exists, label it as the sole eligible server. This status
does not prove the active connection.

Show the eligible server count before submission. Disable submission when the
result is empty.

For exact server sets, populate only `names`. Clear the other three fields in
the same request.

For hierarchy filters, show the AND and OR rules near the controls. Update the
eligible preview after each selection.

Do not discard current values that the live catalog lacks. Show them as stale
until the user removes them.

Refresh the Gluetun state after each change. Also provide a manual refresh
control.

## Companion HTTP routes

Keep the service API small:

| Method and path               | Purpose                                                       |
| ----------------------------- | ------------------------------------------------------------- |
| `GET /`                       | Render the website                                            |
| `GET /api/state`              | Return filters, catalog age, eligible servers, and VPN status |
| `PUT /api/selection`          | Validate and apply four selector arrays                       |
| `POST /api/connectivity-test` | Test the configured public IP source                          |
| `GET /healthz`                | Report service process health                                 |

Do not create a general Gluetun proxy route. Use explicit upstream methods and
paths.

Validate request sizes and list lengths. Normalize duplicate values with
case-insensitive keys.

Return JSON errors with stable codes and readable messages.

## Connectivity test

The preferred deployment shares the Gluetun network namespace:

```yaml
services:
  companion:
    network_mode: "service:gluetun"
    environment:
      GLUETUN_BASE_URL: http://127.0.0.1:8000
```

If the companion uses another network path, the test reports the companion IP
instead.

Read `GET /v1/publicip/ip` when its route is authorized. Label this value as the
Gluetun public IP.

After a selection change, wait five seconds before the first public IP request.
Retry a failed request two times at five-second intervals.

Show a busy state during the delay and the retries. Keep the last valid public
IP visible until a request succeeds.

Do not infer an active AirVPN server from the public IP. AirVPN entry addresses
can differ from exit addresses.

Return the IP address, request duration, source URL, and observation time. Treat
a malformed IP as a failed test.

## Security

Bind the companion to loopback by default. Require explicit configuration for a
public bind address.

Keep Gluetun authentication on the server side. Do not store its credentials in
JavaScript or HTML.

Add application authentication before exposure outside a trusted network.

Escape all AirVPN and Gluetun values before HTML output. Set restrictive content
security and frame headers.

Do not log complete upstream response bodies. They can contain configuration
details.

## Failure behavior

- Keep the last valid AirVPN catalog after an AirVPN error.
- Disable changes after a Gluetun read error.
- Show `401` as an authentication configuration error.
- Show Gluetun `400` responses next to the selection editor.
- Keep the last confirmed filters after a PUT error.
- Use separate timeouts for AirVPN, Gluetun, and IP checks.
- Cancel upstream requests when the browser request ends.

## Tests

Add unit tests for these cases:

- AirVPN `health=ok` inclusion.
- AirVPN warning and error exclusion.
- `Toronto, Ontario` conversion to `Toronto Ontario`.
- Invalid JSON, address, and required field types.
- Multiple values within one field use OR.
- Multiple non-empty fields use AND.
- Case-insensitive comparisons.
- Duplicate selector removal.
- Empty final intersection rejection.
- Full PUT payload with explicit empty arrays.
- Required API key authentication.
- Gluetun `400`, `401` timeout, and connection failures.
- Stale catalog fallback.
- Public IP response validation.

Use recorded JSON fixtures for unit tests. Do not depend on live AirVPN or ipify
services during normal tests.

Add one optional live contract test for the AirVPN schema. Keep it disabled
unless an explicit environment variable enables it.

## Acceptance criteria

- The page shows Gluetun's filter state (GET /v1/vpn/settings) and public ip
  (GET /v1/publicip/ip).
- The page shows the currently selected AirVPN servers that match the applied
  filters.
- Thage page shows potential AirVPN servers that could be selected, separated by
  hierarchy.
- Multi-value previews match Gluetun's OR and AND rules.
- The service sends all four selector arrays in each update.
- A failed update leaves the last confirmed page state intact and shows the
  error message.

## Source references

- [AirVPN live status API](https://airvpn.org/api/status/)
- [Gluetun AirVPN API model](https://github.com/passteque/gluetun/blob/master/internal/provider/airvpn/updater/api.go)
- [Gluetun AirVPN server conversion](https://github.com/passteque/gluetun/blob/master/internal/provider/airvpn/updater/servers.go)
- [Gluetun server filter logic](https://github.com/passteque/gluetun/blob/master/internal/storage/filter.go)
- [Gluetun server selection settings](https://github.com/passteque/gluetun/blob/master/internal/configuration/settings/serverselection.go)
- [Gluetun VPN settings API](https://github.com/passteque/gluetun/blob/master/internal/server/vpn.go)
- [Gluetun API key header](https://github.com/passteque/gluetun/blob/master/internal/server/middlewares/auth/apikey.go)
- [Gosettings CSV parser](https://pkg.go.dev/github.com/qdm12/gosettings/reader#Reader.CSV)

- [Starlette](https://starlette.dev/)

<script src="[https://cdn.jsdelivr.net/npm/htmx.org@4.0.0](https://cdn.jsdelivr.net/npm/htmx.org@4.0.0)" integrity="sha384-BvJpBiO8Kh31EqtJe5DRIeWrHWnCGkwytKs9NKFi86Hhw96dEqdEMzZDeK9iEGTc" crossorigin="anonymous"></script>

(yes, i want you to use htmx 4, newly released,
[https://four.htmx.org/llms.txt](https://four.htmx.org/llms.txt)
[https://four.htmx.org/llms-full.txt](https://four.htmx.org/llms-full.txt))

you should provide a nice interface for all the servers, pull in some frontend
fuzzy search library
(<script src="[https://cdn.jsdelivr.net/npm/fuse.js/dist/fuse.min.mjs](https://cdn.jsdelivr.net/npm/fuse.js/dist/fuse.min.mjs)"></script>).
display sections for each type: SERVER_COUNTRIES, SERVER_REGIONS, SERVER_CITIES
SERVER_NAMES, cards for each option. (you can see in
/home/tim/code/gluetun/internal/provider/airvpn/updater/servers.go how this list
comes from [https://airvpn.org/api/status/](https://airvpn.org/api/status/) and
gets parsed in a way that gluetun understands)

you should have a separate section for the selected servers/cards at the top.

all cards should have a way to toggle them.

the cards should also display bandwidth (given in megabits per second) and the
users connected. is there a reliable way to get the flag for the country based
on the country_code in emoji?

for auth to this service, simply use 2-week cookie auth, where the value is the
X-API-Key used by gluetun. if you insist, you can encrypt/salt the API key with
some in-memory cipher key so that the cookie value isn't different than the
actual API key and its totes fine that restarts of the service will invalidate
the cookie. in other words, to serve the config page, you'd need to hit some
gluetun control server endpoints, and you'd make that request with the browser
request's cookie, decrypted with the in-memory key. if there is no cookie or
auth fails (401 unauth from gluetun), then you can just kick the user out to
login page.

don't get too crazy with the css. dark mode only, don't make it too nice.

use some fonts:

```xml
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Commissioner:slnt,wght,FLAR,VOLM@-12..0,100..900,0..100,0..100&family=JetBrains+Mono:ital,wght@0,100..800;1,100..800&display=swap" rel="stylesheet">
```

```scss
// <weight>: Use a value from 100 to 900
// <slant>: Use a value from -12 to 0
// <flare>: Use a value from 0 to 100
// <volume>: Use a value from 0 to 100
// <uniquifier>: Use a unique and descriptive class name
.commissioner-<uniquifier > {
  font-family: "Commissioner", sans-serif;
  font-optical-sizing: auto;
  font-weight: <weight>;
  font-style: normal;
  font-variation-settings:
    "slnt" <slant>,
    "FLAR" <flare>,
    "VOLM" <volume>;
}

// <weight>: Use a value from 100 to 800
// <uniquifier>: Use a unique and descriptive class name
.jetbrains-mono-<uniquifier > {
  font-family: "JetBrains Mono", monospace;
  font-optical-sizing: auto;
  font-weight: <weight>;
  font-style: normal;
}
```

## Live gluetun server.

I'm running a live gluetun server at `http://localhost:8000/`, api key is
`test`.
