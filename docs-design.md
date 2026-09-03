# CAIC agentic search — design notes and prototype

A working spike for answering questions like *"is Berthoud Pass safe today"* against
Colorado Avalanche Information Center data, with a season of context behind the answer.

```
python -m avalanche ingest --start 2025-11-01 --end 2026-04-30   # backfill observations
python -m avalanche weather --start 2025-11-01 --end 2026-04-30  # backfill SNOTEL snowpack weather
python -m avalanche snapshot                                     # archive today's forecast (daily cron)
python -m avalanche digests --out digests/                       # human-readable Markdown mirror
python -m avalanche ask "is berthoud pass safe today"            # grounded brief
```

## What the data actually looks like

Three findings from probing the live API drove every design decision here.

**1. Observations are structured, not prose.** An avalanche record arrives with
`aspect`, `elevation` band, `destructive_size`, `problem_type`, `primary_trigger`,
`weak_layer`, `latitude`/`longitude` and zone as enumerated fields. Across a 150-report
sample from January 2026, free text ran a median of ~400 characters — a sentence or two
of observer colour. The signal is in the columns.

**2. Forecast history is available, but the parameters fight each other.** The
`api-proxy/avid` products endpoint honours `datetime` — *unless* `includeExpired=true` is
sent alongside it, in which case it returns today's products and silently ignores the date.
An early version of this project sent both and concluded, wrongly, that no archive existed.
The site's own React bundle branches between the two rather than combining them. Forecast
areas are also grouped dynamically per day, so locations must be matched geometrically
against each date's area GeoJSON rather than by name.

**3. Zones are not places.** CAIC forecasts "Front Range". People ask about Berthoud Pass.
Nothing upstream bridges that, and it is the highest-leverage piece to own.

## Why this is structured-first, not RAG-first

"Is Berthoud Pass safe today" decomposes into an aggregation, not a similarity search:

> On the aspects and elevation bands that exist at this location, what has been failing,
> how recently, on what weak layer — and what does today's forecast say about those same
> aspects and bands?

Embedding 400-character observation blurbs and retrieving the top-k nearest ones answers a
different question: it returns anecdotes that *sound* like the query. It cannot count, it
cannot filter to the terrain that exists at a location, and it cannot tell you that
21 of this season's slides in your zone were on east-facing slopes above treeline.

So the pipeline is: **SQL for the risk profile, text as supporting evidence, LLM for
narration.** The `ask` command emits a structured brief; a model turns it into prose. The
model never has to *discover* the risk profile, which is where hallucination would be
genuinely dangerous.

Retrieval still earns a place, just not the central one — as a second pass over the
observer narrative and forecast discussion, once the structured query has already fixed
*which* days, zones and aspects matter. Filter first, embed second. The Markdown digests
in `digests/` are shaped to be that corpus when you want it.

## Where the leverage is

The aspect × elevation rose is the natural join key across all three data sources: it is
how forecasts express danger, how observations record what slid, and how terrain is
described. Getting `locations.py` right — ideally DEM-derived per named zone rather than
hand-curated — is what turns a zone-level feed into a place-level answer.

## On `caic-python`

Worth using for observations; its `AvalancheObservation` model validated cleanly against
live payloads, enums and all. Two things to know before adopting it (both current as of
v0.2.0, released July 2026):

- **It will not `pip install` as-is.** `pyproject.toml` pins `pydantic==2.14.*`, which
  only exists on PyPI as pre-releases (`2.14.0a1`/`b1`). Installing needs `--pre` or a
  relaxed pin.
- **`avy_forecast()` returns nothing.** Its `AvalancheForecast` model requires `title` and
  `confidence`; the live payload supplies `publicName` and omits `confidence` entirely.
  The `ValidationError` is caught and logged, so the failure is silent — you get an empty
  list, not an exception.

This prototype therefore reads observations through the same v2 endpoints the library
wraps, and parses forecasts directly (`caic.py`). Swapping the observation path back onto
`caic-python` is a small change if you would rather track upstream.

## Weather: batch history, not a request-time lookup

Weather enters this system through the NRCS **SNOTEL** network (`weather.py`), not through
a general weather API, for two reasons.

**Relevance.** Avalanche-relevant weather is not general weather. What matters is how much
water arrived, how fast, and whether the surface warmed. SNOTEL measures water equivalent,
snow depth and temperature directly, and in Colorado its sites sit remarkably close to the
terrain people ski — the Berthoud Summit site is **0.4 miles from the pass at 11,300 ft**.
A hyper-local surface forecast for the same coordinates is not a substitute for a
co-located snow pillow.

**Shape.** The value is in the joined season history, which is a time-series operation
rather than a point query. Ingesting the 2025-26 season and asking about 9 March 2026
produces:

> - Loading days in window: 2026-02-25, -26, -27, 2026-03-06, -07, -08
> - **37 of the recent avalanches ran within 48 hours of a loading day at the nearest SNOTEL site.**

The March 6-8 storm loaded a persistent weak layer and a wave of D2 persistent slabs ran on
N/NE above treeline over the following 72 hours. Neither dataset tells that story alone,
and no single request-time API call surfaces it.

So a connected weather MCP is **complementary, not a substitute**: it is a good fit for the
"what is it doing right now and for the next 24 hours" narration layer, and a poor fit for
backfilling a season, which would be hundreds of tool calls against APIs that mostly do not
expose deep daily history anyway. Wire one in for nowcasting; keep the batch ingest for
context.

### The wind gap is still open

Wind was the single most common problem in the sample (20 of 43 slides at Berthoud), and
SNOTEL does not measure it. The probing done here found no free JSON route to ridgetop
wind: `api.avalanche.state.co.us` has no weather-station endpoint, the `classic.` host
serves HTML plots, and Synoptic/MesoWest — which aggregates CAIC's own stations and is the
right answer — requires a token (free for non-commercial use). That token is the cheapest
next unlock in the whole system.

## MCP server

`mcp_server.py` exposes the whole layer over the Model Context Protocol, so any MCP
client can ask about conditions directly.

```bash
pip install -e ".[mcp]"
export CAIC_DB=/path/to/caic.db
caic-mcp                                  # stdio
MCP_TRANSPORT=streamable-http caic-mcp    # HTTP
```

Claude Code / Claude Desktop config:

```json
{
  "mcpServers": {
    "caic": {
      "command": "caic-mcp",
      "env": { "CAIC_DB": "/absolute/path/to/caic.db" }
    }
  }
}
```

### The tool surface is answers, not endpoints

The tools deliberately do **not** mirror the CAIC API. A faithful wrapper would expose
paginated observation endpoints, and a model asking "is Berthoud Pass safe" would pull
hundreds of records into its context and aggregate them itself — slow, expensive, and
exactly where a model starts inventing numbers. Here the aggregation happens in SQL and
only the result crosses into the context window.

| Tool | Returns |
|---|---|
| `risk_brief` | The whole answer in one call — danger ratings, season aspect/elevation rose, dominant problems, SNOTEL loading, recent slides |
| `list_locations` | Known locations with zone and terrain |
| `avalanche_counts` | Counts grouped by aspect / band / problem / size — not records |
| `snowpack_weather` | Daily SNOTEL series with loading and warming flags |
| `zone_digest` | The narrative behind one zone-day |
| `current_forecast` | Live from CAIC, bypassing the local archive |

`risk_brief` is marked as the primary tool in the server instructions so a client reaches
for it first instead of composing the smaller ones. Every tool is annotated
`read_only_hint`, so a host can run them in parallel without gating. The server also
publishes a `caic://coverage` resource stating what the database actually holds and where
the gaps are — an agent that cannot see coverage will happily answer from an empty table.

The server's instructions carry the one piece of domain judgement that matters: report
season context alongside the current rating, and never present this as a substitute for
the official CAIC forecast.

## Not yet covered

- **Ridgetop wind**, per above — needs a Synoptic token.
- **Danger-rating history** is only as deep as your snapshot archive.
- The gazetteer covers six locations as a starting point.
