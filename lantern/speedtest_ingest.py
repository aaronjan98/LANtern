#!/usr/bin/env python3
"""Run an Ookla speedtest on a schedule and store results in LANtern.

The "internet health" half of Phase 1: ISP speed over time. Each cycle runs the
official Ookla CLI (`speedtest --format=json`) and writes one row to
speedtest_results -- download/upload throughput, idle latency, jitter, packet
loss, plus the chosen server and the shareable result URL.

A failed run (e.g. the WAN is down) is recorded with ok=0 and the error text, so
gaps in the speed history are explicit rather than silent. Continuous outage
detection proper lives in the WAN connectivity collector, not here.

A speedtest briefly saturates the uplink (~15-30s), so it is idle-gated: each
tick it asks the activity oracle whether the *internet* is idle and only runs
when nobody is using the WAN. Guardrails keep it honest -- it never tests more
often than MIN_INTERVAL, and if the internet stays busy for HARD_CAP it runs
anyway so the speed history never fully starves.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request

from lantern.common import DEFAULT_DB, connect_db, get_metadata, set_metadata

LAST_SUCCESS_KEY = "speedtest.last_success_unix"


def run_speedtest(server_id: int | None, timeout: float) -> dict:
    """Invoke the Ookla CLI and return its parsed JSON, or raise on failure."""
    cmd = ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"]
    if server_id:
        cmd += ["--server-id", str(server_id)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "speedtest failed").strip()
        raise RuntimeError(msg[:500])
    return json.loads(proc.stdout)


def parse_result(data: dict) -> dict:
    """Map the Ookla JSON into our row. Ookla 'bandwidth' is bytes/s -> bits/s."""
    dl = data.get("download") or {}
    ul = data.get("upload") or {}
    ping = data.get("ping") or {}
    server = data.get("server") or {}
    result = data.get("result") or {}
    return {
        "download_bps": (dl.get("bandwidth") or 0) * 8 or None,
        "upload_bps": (ul.get("bandwidth") or 0) * 8 or None,
        "ping_ms": ping.get("latency"),
        "jitter_ms": ping.get("jitter"),
        "packet_loss": data.get("packetLoss"),
        "server_name": server.get("name"),
        "server_id": server.get("id"),
        "isp": data.get("isp"),
        "result_url": result.get("url"),
    }


def insert(conn, ts: int, ok: int, rec: dict, error: str | None) -> None:
    conn.execute(
        """
        INSERT INTO speedtest_results
            (ts_unix, ok, download_bps, upload_bps, ping_ms, jitter_ms,
             packet_loss, server_name, server_id, isp, result_url, error,
             ingested_unix)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts, ok,
            rec.get("download_bps"), rec.get("upload_bps"), rec.get("ping_ms"),
            rec.get("jitter_ms"), rec.get("packet_loss"), rec.get("server_name"),
            rec.get("server_id"), rec.get("isp"), rec.get("result_url"),
            error, int(time.time()),
        ),
    )
    conn.commit()


def one_cycle(conn, server_id: int | None, timeout: float) -> bool:
    """Run one test; return True on success. Records an ok=0 row on failure."""
    ts = int(time.time())
    try:
        rec = parse_result(run_speedtest(server_id, timeout))
        insert(conn, ts, 1, rec, None)
        set_metadata(conn, LAST_SUCCESS_KEY, str(ts))
        conn.commit()
        dl = (rec["download_bps"] or 0) / 1e6
        ul = (rec["upload_bps"] or 0) / 1e6
        print(f"[{ts}] ok  down={dl:.1f}Mbps up={ul:.1f}Mbps "
              f"ping={rec['ping_ms']}ms loss={rec['packet_loss']}%", flush=True)
        return True
    except Exception as e:  # noqa: BLE001 -- any failure is a recorded ok=0 row
        insert(conn, ts, 0, {}, str(e))
        print(f"[{ts}] FAILED: {e}", flush=True)
        return False


def internet_idle(api_url: str, timeout: float):
    """Ask the oracle whether the WAN is idle. True/False, or None if unreachable."""
    try:
        with urllib.request.urlopen(api_url, timeout=timeout) as r:
            data = json.loads(r.read())
        return bool(data["internet_idle"])
    except Exception:  # noqa: BLE001 -- oracle down => unknown
        return None


def _seed_last_success(conn) -> None:
    """First run: adopt the last real test time so we respect MIN_INTERVAL."""
    if get_metadata(conn, LAST_SUCCESS_KEY) is not None:
        return
    row = conn.execute(
        "SELECT MAX(ts_unix) AS ts FROM speedtest_results WHERE ok = 1"
    ).fetchone()
    set_metadata(conn, LAST_SUCCESS_KEY, str(int(row["ts"] or 0)))
    conn.commit()


def decide(conn, args) -> tuple[bool, str]:
    """Gate a test on age + oracle internet-idle, with a hard-cap safety net."""
    last = int(get_metadata(conn, LAST_SUCCESS_KEY) or 0)
    age_h = (int(time.time()) - last) / 3600
    if age_h < args.min_interval_h:
        return False, f"tested {age_h:.1f}h ago (< {args.min_interval_h}h) — waiting"
    idle = internet_idle(args.api_url, args.api_timeout)
    if idle is True:
        return True, f"internet idle (age {age_h:.1f}h)"
    if age_h >= args.hard_cap_h:
        return True, f"hard cap {age_h:.0f}h ≥ {args.hard_cap_h}h — running despite busy"
    state = "busy" if idle is False else "oracle unreachable"
    return False, f"internet {state} (age {age_h:.1f}h) — deferring to next tick"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--tick", type=float, default=900,
                    help="seconds between gate checks (default 15m)")
    ap.add_argument("--min-interval-h", type=float, default=2,
                    help="never test more often than this (default 2h)")
    ap.add_argument("--hard-cap-h", type=float, default=12,
                    help="test even if the internet is busy past this age (default 12h)")
    ap.add_argument("--api-url", default="http://lantern.home/api/activity/idle",
                    help="oracle endpoint providing internet_idle")
    ap.add_argument("--api-timeout", type=float, default=10)
    ap.add_argument("--server-id", type=int, default=None,
                    help="pin a specific Ookla server id (default: auto)")
    ap.add_argument("--timeout", type=float, default=120,
                    help="per-test timeout in seconds")
    ap.add_argument("--once", action="store_true",
                    help="run a single test now, bypassing the gate, and exit")
    args = ap.parse_args()

    conn = connect_db(args.db)  # provisions speedtest_results via SCHEMA

    if args.once:  # manual "run a speedtest now"
        one_cycle(conn, args.server_id, args.timeout)
        return 0

    _seed_last_success(conn)
    while True:
        run, reason = decide(conn, args)
        if run:
            print(f"[{int(time.time())}] PROCEEDING: {reason}", flush=True)
            one_cycle(conn, args.server_id, args.timeout)
        else:
            print(f"[{int(time.time())}] skip: {reason}", flush=True)
        time.sleep(args.tick)


if __name__ == "__main__":
    raise SystemExit(main())
