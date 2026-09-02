#!/usr/bin/env python3
"""Drive the MCP server over stdio exactly as a client would.

This is the check to run before wiring the server into Claude: it launches the
real process, completes the protocol handshake, lists the tools and calls one.

Usage:
    python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    db = os.environ.get("CAIC_DB", "caic.db")
    if not os.path.exists(db):
        print(f"No database at {db!r}. Run `make seed` first.", file=sys.stderr)
        return 1

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "avalanche.mcp_server"],
        env={**os.environ, "CAIC_DB": os.path.abspath(db)},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print(f"connected to {info.server_info.name} v{info.server_info.version}\n")

            tools = (await session.list_tools()).tools
            print(f"{len(tools)} tools:")
            for tool in tools:
                ro = getattr(tool.annotations, "read_only_hint", None)
                print(f"  {tool.name:<18} read_only={ro}")

            coverage = await session.read_resource("caic://coverage")
            print("\n" + coverage.contents[0].text)

            print("\n--- risk_brief: Berthoud Pass ---")
            result = await session.call_tool("risk_brief", {"location": "Berthoud Pass"})
            print(result.content[0].text)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
