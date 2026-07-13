#!/usr/bin/env python3
"""Continuously probe WAN reachability into LANtern (wan_checks).

The "is the internet up right now?" collector. Every cycle it opens a short TCP
connection to a set of external anchors (Cloudflare / Google / Quad9 on 443). If
any connects, the internet is up and we record the best round-trip time; if all
fail, it's down.

To tell a real outage apart from "connected but no internet", it also probes the
LAN gateway. gateway_up=1 while up=0 is the classic WAN-down-but-LAN-fine case;
gateway_up=0 means the local link itself is gone.

Pure stdlib sockets -- no ICMP, so no raw-socket capability is needed in the
container. Outage *windows* are derived at query time in the API from these rows.
"""
from __future__ import annotations

import argparse
import json
import socket
import time

from lantern.common import DEFAULT_DB, connect_db

# (host, port) external anchors on three independent networks (Cloudflare,
# Google, OpenDNS) — up if ANY connects. Quad9's 9.9.9.9 is intentionally absent:
# it does not answer TCP/443 from this network, which would fake constant loss.
DEFAULT_TARGETS = "1.1.1.1:443,8.8.8.8:443,208.67.222.222:443"
DEFAULT_GATEWAY = "10.0.50.1:80"


def _probe(host: str, port: int, timeout: float) -> float | None:
    """Return connect RTT in ms, or None on failure."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.monotonic() - start) * 1000.0
    except OSError:
        return None


def _parse_targets(spec: str) -> list[tuple[str, int]]:
    out = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        host, _, port = item.partition(":")
        out.append((host, int(port or 443)))
    return out


def check(targets, gateway, timeout: float) -> dict:
    results = {}
    rtts = []
    ok = 0
    for host, port in targets:
        rtt = _probe(host, port, timeout)
        results[f"{host}:{port}"] = rtt
        if rtt is not None:
            ok += 1
            rtts.append(rtt)

    gateway_up = None
    if gateway:
        gh, gp = gateway
        grtt = _probe(gh, gp, timeout)
        results[f"gateway {gh}:{gp}"] = grtt
        gateway_up = 1 if grtt is not None else 0

    return {
        "up": 1 if ok > 0 else 0,
        "gateway_up": gateway_up,
        "rtt_ms": min(rtts) if rtts else None,
        "targets_ok": ok,
        "targets_total": len(targets),
        "detail": json.dumps(results),
    }


def insert(conn, ts: int, r: dict) -> None:
    conn.execute(
        """
        INSERT INTO wan_checks
            (ts_unix, up, gateway_up, rtt_ms, targets_ok, targets_total, detail,
             ingested_unix)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts, r["up"], r["gateway_up"], r["rtt_ms"], r["targets_ok"],
            r["targets_total"], r["detail"], int(time.time()),
        ),
    )
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--interval", type=float, default=30, help="seconds between checks")
    ap.add_argument("--timeout", type=float, default=3, help="per-probe TCP timeout")
    ap.add_argument("--targets", default=DEFAULT_TARGETS, help="comma list host:port")
    ap.add_argument("--gateway", default=DEFAULT_GATEWAY, help="LAN gateway host:port (or '')")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    targets = _parse_targets(args.targets)
    gateway = _parse_targets(args.gateway)[0] if args.gateway else None

    conn = connect_db(args.db)  # provisions wan_checks via SCHEMA
    prev_up = None
    while True:
        ts = int(time.time())
        r = check(targets, gateway, args.timeout)
        insert(conn, ts, r)
        if r["up"] != prev_up:  # log only on state transitions
            state = "UP" if r["up"] else ("DOWN (LAN ok, no internet)"
                                          if r["gateway_up"] else "DOWN (link down)")
            print(f"[{ts}] internet {state}  rtt={r['rtt_ms']}", flush=True)
            prev_up = r["up"]
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
