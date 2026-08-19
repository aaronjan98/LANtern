#!/usr/bin/env python3
"""Idle-gated Deco WiFi reboot — the scheduler/brain half.

Why this exists: the main Deco X60 (10.0.50.216) has a recurring 5 GHz
uptime-degradation fault that only a power-cycle clears (see the LANtern network
notes). The reboot actuator (~/deco-reboot/deco-reboot.sh) already exists but was
never scheduled, so the unit rots for weeks. This module is the "brain" that was
missing: it decides *when* a reboot is due and gates it on the activity oracle so
the AP is only cycled when nobody is using WiFi.

Split of responsibilities (the actuator lives on the host, not in a container):
  * THIS module (a container on the shared lantern image) owns the state machine
    in ``deco_reboot_events``: it creates the ~N-day backstop request, and flips
    an approved request to ``ready`` once the oracle reports safe_for_deco_reboot
    (internet_idle && homelab_idle -- a reboot drops all WiFi).
  * The host runner (deco-reboot-runner.sh, a systemd timer) claims ``ready``
    rows via ``--claim-ready``, runs deco-reboot.sh (it holds the Kasa creds),
    and reports back via ``--complete``.

Hybrid policy (per the idle-automation spec §6): the scheduled backstop is
auto-approved (no human prompt) but still idle-gated; a person can also request
a reboot now or cancel a pending one from the UI. There is deliberately **no
deadline** -- a reboot never forces itself during use; it waits for an idle
window, which on this low-user homelab always comes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

from lantern.common import DEFAULT_DB, connect_db, get_metadata, set_metadata

LAST_SUCCESS_KEY = "deco_reboot.last_success_unix"
SEEDED_KEY = "deco_reboot.seeded"

# Requests that are still "in flight" -- while any of these exist we don't open
# a new backstop, and only one may be ready/executing at a time.
OPEN_STATUSES = ("pending", "approved", "ready", "executing")


def _connect(db_path: str):
    conn = connect_db(db_path)  # provisions deco_reboot_events via SCHEMA
    conn.execute("PRAGMA busy_timeout=5000")  # tolerate the collectors' writes
    return conn


def _now() -> int:
    return int(time.time())


def _open_request(conn):
    return conn.execute(
        f"SELECT * FROM deco_reboot_events WHERE status IN "
        f"({','.join('?' * len(OPEN_STATUSES))}) ORDER BY created_unix LIMIT 1",
        OPEN_STATUSES,
    ).fetchone()


def _last_success_unix(conn) -> int:
    """Newest confirmed reboot: prefer our own log, fall back to metadata seed."""
    row = conn.execute(
        "SELECT MAX(finished_unix) AS ts FROM deco_reboot_events WHERE status='succeeded'"
    ).fetchone()
    from_events = int(row["ts"] or 0)
    from_meta = int(get_metadata(conn, LAST_SUCCESS_KEY) or 0)
    return max(from_events, from_meta)


def _seed_if_first_run(conn) -> None:
    """First deploy: adopt 'now' as the last success so the backstop waits a full
    interval instead of firing an unexpected power-cycle the moment we start. The
    user can still trigger the first reboot immediately from the UI."""
    if get_metadata(conn, SEEDED_KEY):
        return
    if _last_success_unix(conn) == 0:
        set_metadata(conn, LAST_SUCCESS_KEY, str(_now()))
    set_metadata(conn, SEEDED_KEY, "1")
    conn.commit()


def oracle_safe(api_url: str, timeout: float):
    """Ask the oracle if it's safe to reboot. (True/False, evidence) or (None, {})."""
    try:
        with urllib.request.urlopen(api_url, timeout=timeout) as r:
            data = json.loads(r.read())
        return bool(data["safe_for_deco_reboot"]), data
    except Exception:  # noqa: BLE001 -- oracle down => unknown, try again next tick
        return None, {}


def _create_backstop(conn, interval_days: float) -> None:
    """Open an auto-approved backstop request if one is due and none is in flight."""
    if _open_request(conn) is not None:
        return
    age_days = (_now() - _last_success_unix(conn)) / 86400
    if age_days < interval_days:
        return
    now = _now()
    conn.execute(
        """
        INSERT INTO deco_reboot_events
            (created_unix, trigger, status, requested_by, approved_by,
             approved_unix, updated_unix)
        VALUES (?, 'scheduled_backstop', 'approved', 'scheduler', 'scheduler', ?, ?)
        """,
        (now, now, now),
    )
    conn.commit()
    print(f"[{now}] opened backstop request (last reboot {age_days:.1f}d ago "
          f"≥ {interval_days}d)", flush=True)


def _promote_if_idle(conn, api_url: str, timeout: float) -> None:
    """Flip the oldest approved request to 'ready' when the oracle says it's safe.

    Only one reboot may be ready/executing at a time. No hard cap: an approved
    reboot waits indefinitely for an idle window rather than forcing itself.
    """
    if conn.execute(
        "SELECT 1 FROM deco_reboot_events WHERE status IN ('ready','executing') LIMIT 1"
    ).fetchone():
        return
    row = conn.execute(
        "SELECT * FROM deco_reboot_events WHERE status='approved' "
        "ORDER BY created_unix LIMIT 1"
    ).fetchone()
    if row is None:
        return
    safe, evidence = oracle_safe(api_url, timeout)
    now = _now()
    if safe is True:
        conn.execute(
            "UPDATE deco_reboot_events SET status='ready', ready_unix=?, "
            "idle_evidence_json=?, updated_unix=? WHERE id=? AND status='approved'",
            (now, json.dumps(evidence), now, row["id"]),
        )
        conn.commit()
        print(f"[{now}] request #{row['id']} READY — oracle says safe_for_deco_reboot",
              flush=True)
    else:
        state = "busy" if safe is False else "oracle unreachable"
        print(f"[{now}] request #{row['id']} approved but {state} — waiting", flush=True)


def tick(conn, args) -> None:
    _create_backstop(conn, args.interval_days)
    _promote_if_idle(conn, args.api_url, args.api_timeout)


# --- CLI actions used by the host runner and by humans ---------------------

def claim_ready(conn) -> int:
    """Atomically claim the oldest ready request for execution; print its JSON."""
    row = conn.execute(
        "SELECT * FROM deco_reboot_events WHERE status='ready' "
        "ORDER BY ready_unix LIMIT 1"
    ).fetchone()
    if row is None:
        return 0
    now = _now()
    cur = conn.execute(
        "UPDATE deco_reboot_events SET status='executing', executed_unix=?, "
        "updated_unix=? WHERE id=? AND status='ready'",
        (now, now, row["id"]),
    )
    conn.commit()
    if cur.rowcount != 1:  # someone else claimed it between SELECT and UPDATE
        return 0
    print(json.dumps({"id": row["id"], "trigger": row["trigger"]}))
    return 0


def complete(conn, args) -> int:
    """Finalize an executing request with its result + before/after tunnel checks."""
    status = "succeeded" if args.result == "success" else "failed"
    now = _now()
    conn.execute(
        "UPDATE deco_reboot_events SET status=?, result=?, finished_unix=?, "
        "tunnel_before=?, tunnel_after=?, detail=?, updated_unix=? WHERE id=?",
        (status, status, now, args.tunnel_before, args.tunnel_after,
         args.detail, now, args.id),
    )
    if status == "succeeded":
        set_metadata(conn, LAST_SUCCESS_KEY, str(now))
    conn.commit()
    print(f"[{now}] request #{args.id} -> {status}", flush=True)
    return 0


def request_now(conn, args) -> int:
    """Create an auto-approved manual reboot (CLI parity with the UI button)."""
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO deco_reboot_events
            (created_unix, trigger, status, requested_by, approved_by,
             approved_unix, updated_unix)
        VALUES (?, 'manual', 'approved', ?, ?, ?, ?)
        """,
        (now, args.by, args.by, now, now),
    )
    conn.commit()
    print(f"created manual request #{cur.lastrowid} (approved, waiting for idle)")
    return 0


def show_status(conn) -> int:
    last = _last_success_unix(conn)
    age = (_now() - last) / 86400 if last else None
    print(f"last successful reboot: "
          f"{'never' if not last else f'{age:.1f}d ago'}")
    rows = conn.execute(
        "SELECT id, trigger, status, created_unix FROM deco_reboot_events "
        "ORDER BY created_unix DESC LIMIT 10"
    ).fetchall()
    for r in rows:
        print(f"  #{r['id']:<4} {r['status']:<10} {r['trigger']:<18} "
              f"{time.strftime('%F %T', time.localtime(r['created_unix']))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--tick", type=float, default=600,
                    help="seconds between brain ticks (default 10m)")
    ap.add_argument("--interval-days", type=float, default=3,
                    help="backstop cadence: reboot if idle & this long since last "
                         "(default 3d, matching the accelerated fault interval)")
    ap.add_argument("--api-url", default="http://lantern.home/api/activity/idle",
                    help="oracle endpoint providing safe_for_deco_reboot")
    ap.add_argument("--api-timeout", type=float, default=10)
    # one-shot actions (host runner / humans); default is the brain loop
    ap.add_argument("--claim-ready", action="store_true",
                    help="claim the oldest ready request -> executing; print JSON")
    ap.add_argument("--complete", dest="id", type=int, default=None,
                    help="finalize request ID (needs --result)")
    ap.add_argument("--result", choices=("success", "fail"), default=None)
    ap.add_argument("--tunnel-before", default=None)
    ap.add_argument("--tunnel-after", default=None)
    ap.add_argument("--detail", default=None)
    ap.add_argument("--request", action="store_true",
                    help="create an approved manual reboot request now")
    ap.add_argument("--by", default="cli", help="identity for --request")
    ap.add_argument("--status", action="store_true", help="print recent state and exit")
    ap.add_argument("--once", action="store_true", help="run one brain tick and exit")
    args = ap.parse_args()

    conn = _connect(args.db)

    if args.claim_ready:
        return claim_ready(conn)
    if args.id is not None:
        if not args.result:
            print("--complete requires --result", file=sys.stderr)
            return 2
        return complete(conn, args)
    if args.request:
        return request_now(conn, args)
    if args.status:
        return show_status(conn)

    _seed_if_first_run(conn)
    if args.once:
        tick(conn, args)
        return 0
    print(f"[{_now()}] deco-reboot brain up: backstop every {args.interval_days}d, "
          f"idle-gated via {args.api_url}", flush=True)
    while True:
        try:
            tick(conn, args)
        except Exception as e:  # noqa: BLE001 -- never let the brain die on a tick
            print(f"[{_now()}] tick error: {e}", file=sys.stderr, flush=True)
        time.sleep(args.tick)


if __name__ == "__main__":
    raise SystemExit(main())
