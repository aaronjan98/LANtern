#!/usr/bin/env python3
"""Ingest Tailscale peer presence into LANtern (tailscale_peers).

Fully passive: ONE LocalAPI /status read per interval returns every tailnet
peer's online/offline state -- no per-device pings. This is what lets the People
page distinguish "remote (Tailscale)" from "home (LAN)" without probing anyone.

Talks to tailscaled's local HTTP API over its unix socket (mounted read-only from
the host), so no `tailscale` binary is needed in the image. tailscaled requires
the anti-CSRF header `Sec-Tailscale: localapi` and Host `local-tailscaled.sock`.
"""
from __future__ import annotations

import argparse
import datetime
import http.client
import json
import socket
import time

from lantern.common import DEFAULT_DB, connect_db

DEFAULT_SOCK = "/run/tailscale/tailscaled.sock"


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, sock_path: str):
        super().__init__("local-tailscaled.sock")
        self._sock_path = sock_path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._sock_path)
        self.sock = s


def fetch_status(sock_path: str, timeout: float = 8) -> dict:
    conn = _UnixConnection(sock_path)
    conn.timeout = timeout
    try:
        conn.request(
            "GET", "/localapi/v0/status",
            headers={"Host": "local-tailscaled.sock", "Sec-Tailscale": "localapi"},
        )
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"localapi status {resp.status}: {body[:200]!r}")
        return json.loads(body)
    finally:
        conn.close()


def _parse_last_seen(value) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _ts_ip(peer: dict) -> str | None:
    ips = peer.get("TailscaleIPs") or []
    return next((ip for ip in ips if ip.startswith("100.")), ips[0] if ips else None)


def ingest(conn, status: dict) -> int:
    now = int(time.time())
    peers = list((status.get("Peer") or {}).values())
    if status.get("Self"):
        peers.append(status["Self"])
    count = 0
    for p in peers:
        ts_ip = _ts_ip(p)
        if not ts_ip:
            continue
        dns_name = (p.get("DNSName") or "").split(".")[0] or None
        conn.execute(
            """
            INSERT INTO tailscale_peers
                (ts_ip, dns_name, hostname, os, online, cur_addr, relay,
                 last_seen_unix, updated_unix)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ts_ip) DO UPDATE SET
                dns_name=excluded.dns_name, hostname=excluded.hostname, os=excluded.os,
                online=excluded.online, cur_addr=excluded.cur_addr, relay=excluded.relay,
                last_seen_unix=excluded.last_seen_unix, updated_unix=excluded.updated_unix
            """,
            (ts_ip, dns_name, p.get("HostName"), p.get("OS"),
             1 if p.get("Online") else 0, p.get("CurAddr") or "", p.get("Relay") or "",
             _parse_last_seen(p.get("LastSeen")), now),
        )
        count += 1
    conn.commit()
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--sock", default=DEFAULT_SOCK)
    ap.add_argument("--interval", type=float, default=60)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    conn = connect_db(args.db)  # provisions tailscale_peers via SCHEMA
    while True:
        try:
            n = ingest(conn, fetch_status(args.sock))
            print(f"[{int(time.time())}] tailscale: {n} peers", flush=True)
        except Exception as e:  # noqa: BLE001 -- keep the collector alive
            print(f"[{int(time.time())}] tailscale ingest error: {e}", flush=True)
        if args.once:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
