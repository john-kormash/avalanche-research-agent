# avalanche-research-agent

Agentic search over Colorado Avalanche Information Center data, exposed as an MCP server.
Answers location-scoped questions ("is Berthoud Pass safe today") by aggregating a season
of avalanche observations, forecasts and SNOTEL snowpack weather.

## Commands

```bash
make setup     # venv + dev dependencies
make test      # offline suite (~2s) — run before every commit
make lint      # ruff
make seed      # populate caic.db for the season (network, several minutes)
make smoke     # drive the MCP server over stdio as a client would
make fixtures  # re-record test payloads from live APIs
```

Ingest is also available per-source: `avalanche ingest|forecasts|weather|snapshot|digests|ask`.

## Architecture

Structured-first, not RAG. CAIC observations arrive as enumerated fields — aspect,
elevation band, destructive size, problem type, trigger, weak layer — so the risk profile
is computed by SQL aggregation and free text is supporting evidence, not the retrieval
surface. `profile.py` holds that aggregation; everything else feeds it.

```
caic.py        CAIC v2 observations, field reports, forecast products, point-in-polygon
weather.py     SNOTEL stations, daily series, loading/warming signals
store.py       SQLite schema, normalization, Markdown digests
locations.py   gazetteer: named places -> zone + terrain
profile.py     the aspect x elevation risk aggregation and its rendering
mcp_server.py  MCP tool surface (tools return answers, not endpoint passthroughs)
```

## Upstream traps

These are undocumented APIs. Each of the following cost real debugging time; the tests
exist to stop them recurring.

- **`datetime` and `includeExpired` are mutually exclusive** on `/products/all`. Sending
  both returns a 200 carrying *today's* products with the date silently dropped. This
  looks like "there is no forecast archive" and isn't. See `tests/test_historical_forecasts.py`.
- **Forecast areas are grouped dynamically.** The same terrain belongs to a
  differently-shaped area day to day, and `publicName` is a list of polygon IDs, not a
  name. Match locations geometrically against each date's area GeoJSON.
- **Elevation bands are HTML-escaped**: `&#62;TL`, not `>TL`. `normalize_band` handles it.
- **v2 collection endpoints return bare lists** with no pagination metadata. Paginate
  until a short page comes back.
- **The forecast payload uses `publicName`, not `title`**, and omits `confidence`. This is
  why `caic-python` 0.2.0's forecast model fails validation and silently returns `[]`.

## Conventions

- Tests run **offline** against recorded payloads in `tests/fixtures/`. `make fixtures`
  re-records; the parsing tests then fail loudly if a depended-on field disappears. CI runs
  that weekly to catch schema drift before a season breaks.
- **Never present a danger rating as current when it isn't.** Avalanche.org's terms require
  ratings be "published and expired accordingly". `_forecast` returns `current`, `expired`,
  `superseded` or `None`; in the `superseded` case ratings are *dropped from the payload*,
  not merely relabelled, so a caller that ignores the flag cannot leak them.
- Safety framing is load-bearing: absent data reads as missing information, never as low
  danger, and output always points at avalanche.state.co.us. This is a research prototype,
  not a forecast.
- MCP tools return aggregates, not records — token cost should scale with the answer, not
  the intermediate data. All are annotated `read_only_hint`.
- `caic.db` is regenerable and gitignored. Don't commit it.

## Known gaps

- Ridgetop wind is uncovered — SNOTEL doesn't measure it and there's no free JSON route.
  Synoptic/MesoWest carries CAIC's own stations and needs a token; that is the cheapest
  remaining unlock.
- The gazetteer is six hand-curated locations. Production wants DEM-derived terrain.
  Adding a location requires re-running `avalanche forecasts` — the geometric match happens
  at ingest.
