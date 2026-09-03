# avalanche-research-agent

Agentic search over [Colorado Avalanche Information Center](https://avalanche.state.co.us)
data, exposed as an MCP server.

CAIC publishes a lot — daily forecasts, field reports, avalanche observations, weather —
but it is scattered across three hosts and several undocumented APIs, and none of it is
joined. This project pulls a season into one queryable store and answers location-scoped
questions against it:

> **is berthoud pass safe today**
>
> Season to date — 108 avalanches on comparable terrain. 40 of them east-facing above
> treeline. Dominant problems: persistent (43), wind (37). 37 of the recent slides ran
> within 48 hours of a loading day at the Berthoud Summit SNOTEL site, 0.4 miles away.

> [!WARNING]
> This is a research prototype, not an avalanche forecast. It reads undocumented APIs
> that can change without notice, and it has known gaps (see [Coverage](#coverage)).
> Always consult the official forecast at avalanche.state.co.us before travelling in
> avalanche terrain.

## Quick start

```bash
make setup     # venv + dependencies
make seed      # pull the current season (a few minutes; hits the network)
make smoke     # drive the MCP server over stdio, as a client would
make test      # offline test suite
```

## Wiring it into Claude

Once `make seed` has populated `caic.db`:

```jsonc
// Claude Code:  ~/.claude.json   ·   Claude Desktop: claude_desktop_config.json
{
  "mcpServers": {
    "caic": {
      "command": "/absolute/path/to/avalanche-research-agent/.venv/bin/caic-mcp",
      "env": { "CAIC_DB": "/absolute/path/to/avalanche-research-agent/caic.db" }
    }
  }
}
```

Both paths must be absolute — the server is launched from an arbitrary working directory.
Verify with `make smoke` first; if that prints the tool list and a brief, the config above
will work.

Then ask: *"is Berthoud Pass safe today?"*

## The tool surface returns answers, not endpoints

The tools deliberately do **not** mirror the CAIC API. A faithful wrapper would expose
paginated observation endpoints, and a model asking whether somewhere is safe would pull
hundreds of records into its context and aggregate them itself — slow, expensive, and
exactly where a model starts inventing numbers. Here the aggregation happens in SQL and
only the result crosses into the context window.

| Tool | Returns |
|---|---|
| `risk_brief` | **Start here.** Danger ratings, the season's aspect/elevation rose, dominant problems, SNOTEL loading, recent slides — the whole answer in one call |
| `list_locations` | Known locations with zone and terrain |
| `avalanche_counts` | Counts grouped by aspect / band / problem / size — not records |
| `snowpack_weather` | Daily SNOTEL series with loading and warming flags |
| `zone_digest` | The narrative behind one zone-day |
| `current_forecast` | Live from CAIC, bypassing the local archive |

Every tool is annotated `read_only_hint`, so a host can run them in parallel without
gating. The server also publishes a `caic://coverage` resource stating what the database
actually holds — an agent that cannot see coverage will answer from an empty table without
noticing.

## Why structured-first, not RAG

Avalanche observations arrive as enumerated fields — aspect, elevation band, destructive
size, problem type, trigger, weak layer, coordinates. Across a 150-report sample, free text
ran a median of ~400 characters. The signal is in the columns.

"Is X safe today" decomposes into an aggregation, not a similarity search: *on the aspects
and elevations that exist here, what has been failing, how recently, on what layer?*
Top-k retrieval over short observation blurbs returns anecdotes that sound like the query;
it cannot count, cannot filter to terrain that exists at a location, and cannot tell you
that 40 of this season's slides were east-facing above treeline.

Retrieval still earns a place — as a second pass over observer narrative, once the
structured query has fixed which days and aspects matter. Filter first, embed second.
`make digests` renders the Markdown corpus for that.

The full design write-up, including the upstream findings behind these choices, is in
[`docs-design.md`](docs-design.md).

## Coverage

| Source | Status |
|---|---|
| Avalanche observations | Full history, queryable by date range |
| Field reports | Full history |
| Forecasts | **Snapshot-only.** CAIC's products endpoint accepts a `datetime` parameter and ignores it — requests for two different dates return identical current products. There is no archive to backfill, so run `make snapshot` daily via cron and history accumulates from then on. Archived ratings are expiry-aware (see [Danger ratings expire](#danger-ratings-expire)) |
| SNOTEL snowpack weather | Full history; water equivalent, depth, temperature |
| Ridgetop wind | **Not covered.** Wind was the second most common problem in-sample, but SNOTEL does not measure it and there is no free JSON route. [Synoptic/MesoWest](https://synopticdata.com) carries CAIC's own stations and needs a token — the cheapest remaining unlock |

## Danger ratings expire

The [Avalanche.org public API terms](https://github.com/NationalAvalancheCenter/Avalanche.org-Public-API-Docs)
state that *"because avalanche danger changes on a day-to-day basis, danger rating displays
must be published and expired accordingly."* A rating is never returned bare. Every archived
forecast carries one of four states:

| State | Behaviour |
|---|---|
| `current` | Issued for the requested date and inside its validity window. Ratings shown, with the expiry time |
| `expired` | Issued for the requested date but past expiry. Ratings shown, headed **EXPIRED** with the caveat before the numbers — legitimate for retrospective questions, never presented as today |
| `superseded` | Nothing was issued for that date. **Ratings are withheld entirely.** The nearest forecast is reported as a pointer, not a substitute |
| absent | Nothing archived at all. Reported as missing data, explicitly *not* a low rating |

The `superseded` case is the one that matters. Before this was added, asking about a date
with no forecast silently returned the nearest earlier day's ratings as if they applied —
which is exactly what the terms forbid.

The MCP server carries the same rule in its instructions, and `current_forecast` labels every
live product `current` or `expired`.

## Testing

The suite runs offline against recorded payloads in `tests/fixtures/`. That matters because
these APIs are undocumented and have already drifted once: the forecast payload moved from
`title` to `publicName`, which silently broke `caic-python`'s model — it catches the
`ValidationError`, logs it, and returns an empty list.

`make fixtures` re-records from the live APIs; the parsing tests then fail loudly if a
field this project depends on has disappeared. CI runs that weekly.

```bash
make test      # offline, ~2s
make lint      # ruff
make fixtures  # re-record, then run make test
```

## Layout

```
avalanche/
  caic.py         CAIC v2 observations + forecast products
  weather.py      SNOTEL stations, daily series, loading/warming signals
  store.py        SQLite schema, normalization, Markdown digests
  locations.py    named places -> zone + terrain (the gazetteer)
  profile.py      the aspect x elevation risk aggregation
  mcp_server.py   MCP tool surface
  cli.py          ingest / weather / snapshot / digests / ask
scripts/
  seed.py             populate a season
  smoke_test.py       stdio client handshake
  refresh_fixtures.py re-record test payloads
```

## Known limitations

- The gazetteer covers six locations by hand. Production wants DEM-derived terrain per
  named zone.
- Forecast history starts the day you begin snapshotting.
- Off-season, CAIC's products endpoint returns polygon ID lists in place of readable zone
  names, so `current_forecast` zone labels are unhelpful until the season starts.
- Everything rides undocumented endpoints. A personal tool is one thing; a public service
  polling them is a conversation worth having with CAIC first.
