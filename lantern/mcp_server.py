#!/usr/bin/env python3
"""LANtern MCP server — exposes the observability data as read-only tools.

This is LANtern's model-agnostic query surface. Any MCP client (Claude Code on
the Framework laptop, a local Ollama agent, a future model) can call these tools
to answer questions about the network in plain language — "who's online?", "was
the internet down last night?", "which device is the top talker?".

Design: the tools are thin proxies over LANtern's own HTTP API, so every piece of
derived logic (outage windows, latency buckets, who's-online, uptime %) is reused
rather than duplicated here. LANtern stays the data/tool provider; the model that
calls these lives elsewhere (the Framework), per the project's architecture.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

API_BASE = os.environ.get("LANTERN_API", "http://api:8000/api")
HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "8765"))

mcp = FastMCP("lantern", host=HOST, port=PORT)


def _get(path: str, **params):
    url = f"{API_BASE}{path}"
    q = {k: v for k, v in params.items() if v is not None}
    if q:
        url += "?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


# ---- people / presence -----------------------------------------------------
@mcp.tool()
def who_is_online() -> list:
    """Which people are online right now and which of their devices are active.
    Use for 'who is home / online?'."""
    return _get("/people/online")


@mcp.tool()
def list_people() -> list:
    """All known people with their device counts and current online status."""
    return _get("/people")


# ---- devices ---------------------------------------------------------------
@mcp.tool()
def list_devices(active_only: bool = False) -> list:
    """Device inventory (mac, hostname, ip, vendor, label, owning person, active).
    Set active_only to return only devices seen in the last ~5 minutes."""
    devs = _get("/devices")
    if active_only:
        devs = [d for d in devs if d.get("active")]
    return devs


@mcp.tool()
def get_device(identifier: str) -> dict:
    """Detail for one device by MAC or IP address, including recent IP history."""
    return _get(f"/devices/{urllib.parse.quote(identifier)}")


# ---- internet health -------------------------------------------------------
@mcp.tool()
def internet_status() -> dict:
    """Current internet health: online/offline, uptime %, recent outage count,
    and the latest measured download/upload/ping."""
    return {
        "connectivity": _get("/internet/connectivity/summary", hours=168),
        "latest_speedtest": _get("/internet/speedtest/latest"),
    }


@mcp.tool()
def recent_outages(hours: int = 168) -> list:
    """Internet outage windows over the last `hours`: start, duration, whether it
    was 'connected but no internet' (LAN up, WAN down) vs a full link-down."""
    return _get("/internet/outages", hours=hours)


@mcp.tool()
def speed_history(hours: int = 168) -> list:
    """ISP speedtest results (download/upload/ping) over the last `hours`."""
    return _get("/internet/speedtest", hours=hours)


@mcp.tool()
def wan_latency(hours: int = 24) -> list:
    """WAN latency and packet-loss, bucketed over the last `hours`."""
    return _get("/internet/latency", hours=hours)


@mcp.tool()
def service_status() -> list:
    """uptime-kuma monitors: each service/host with current up/down status,
    uptime %, and last ping over the last week."""
    return _get("/internet/kuma/monitors", hours=168)


# ---- DNS / traffic ---------------------------------------------------------
@mcp.tool()
def recent_dns(client_ip: str | None = None, limit: int = 30) -> list:
    """Recent DNS queries, optionally scoped to one client IP."""
    return _get("/dns/recent", client_ip=client_ip, limit=limit)


@mcp.tool()
def top_domains(minutes: int = 60, limit: int = 20) -> list:
    """Most-queried DNS domains in the last `minutes`."""
    return _get("/dns/top", minutes=minutes, limit=limit)


@mcp.tool()
def top_talkers(minutes: int = 60) -> dict:
    """Devices moving the most traffic (NetFlow) in the last `minutes`."""
    return _get("/flows/top-talkers", minutes=minutes)


@mcp.tool()
def recent_flows(host: str | None = None, limit: int = 20) -> list:
    """Recent traffic flows, optionally filtered to one host IP."""
    return _get("/flows/recent", host=host, limit=limit)


# ---- security / activity ---------------------------------------------------
@mcp.tool()
def security_summary(hours: int = 168) -> dict:
    """SSH / fail2ban security summary: attempts, attackers, bans, coverage."""
    return _get("/security/summary", hours=hours)


@mcp.tool()
def activity_status() -> dict:
    """The activity oracle: is the homelab and the internet idle right now, with
    the evidence windows. Same signal that gates SnapRAID and the speedtest."""
    return _get("/activity/idle")


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
