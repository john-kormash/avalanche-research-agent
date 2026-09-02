"""MCP surface — tool registration, annotations and behaviour on empty data."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def server(populated, monkeypatch, tmp_path):
    """Point the module-level server at the populated test database."""
    import avalanche.mcp_server as mod

    db = tmp_path / "mcp.db"
    populated.conn.commit()
    import sqlite3

    dest = sqlite3.connect(db)
    populated.conn.backup(dest)
    dest.close()
    monkeypatch.setattr(mod, "DB_PATH", str(db))
    return mod


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


@pytest.mark.anyio
async def test_every_tool_is_registered(server):
    names = {t.name for t in await server.mcp.list_tools()}
    assert names == {
        "risk_brief",
        "list_locations",
        "avalanche_counts",
        "snowpack_weather",
        "zone_digest",
        "current_forecast",
    }


@pytest.mark.anyio
async def test_tools_are_annotated_read_only(server):
    """Hosts rely on this to parallelize without gating."""
    for tool in await server.mcp.list_tools():
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.annotations.read_only_hint is True, f"{tool.name} is not marked read-only"


@pytest.mark.anyio
async def test_every_tool_describes_itself(server):
    for tool in await server.mcp.list_tools():
        assert tool.description and len(tool.description) > 40, f"{tool.name} is under-described"


@pytest.mark.anyio
async def test_risk_brief_returns_a_grounded_brief(server):
    out = text_of(await server.mcp.call_tool("risk_brief", {"location": "Berthoud Pass", "date": "2026-03-10"}))
    assert "# Berthoud Pass" in out
    assert "Season to date" in out


@pytest.mark.anyio
async def test_unknown_location_lists_the_known_ones(server):
    out = text_of(await server.mcp.call_tool("risk_brief", {"location": "Nowhere"}))
    assert "Unknown location" in out
    assert "Berthoud Pass" in out, "error should tell the caller what it can answer"


@pytest.mark.anyio
async def test_avalanche_counts_rejects_an_injected_group_by(server):
    """group_by is interpolated into SQL, so the allow-list is a security control."""
    out = text_of(
        await server.mcp.call_tool(
            "avalanche_counts",
            {"zone_slug": "front-range", "group_by": "aspect; DROP TABLE avalanche"},
        )
    )
    assert "group_by must be one of" in out


@pytest.mark.anyio
async def test_avalanche_counts_returns_counts_not_records(server):
    out = json.loads(
        text_of(await server.mcp.call_tool("avalanche_counts", {"zone_slug": "front-range"}))
    )
    assert set(out) >= {"zone_slug", "grouped_by", "counts", "total"}
    assert isinstance(out["counts"], dict)


@pytest.mark.anyio
async def test_snowpack_weather_reports_the_nearest_station(server):
    out = json.loads(
        text_of(
            await server.mcp.call_tool(
                "snowpack_weather", {"location": "Berthoud Pass", "days": 10, "end": "2026-03-10"}
            )
        )
    )
    assert out["station"] == "Berthoud Summit"
    assert out["miles_from_location"] < 1.0


@pytest.mark.anyio
async def test_zone_digest_says_so_when_a_day_is_empty(server):
    out = text_of(await server.mcp.call_tool("zone_digest", {"zone_slug": "front-range", "date": "1999-01-01"}))
    assert "No observations recorded" in out


@pytest.mark.anyio
async def test_coverage_resource_reports_what_is_held(server):
    body = await server.mcp.read_resource("caic://coverage")
    text = body[0].content if hasattr(body[0], "content") else str(body)
    assert "Avalanche observations" in text
    assert "Known gaps" in text
