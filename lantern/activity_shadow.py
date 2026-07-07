#!/usr/bin/env python3
"""Shadow-mode logger for the activity oracle.

Runs the oracle (lantern.activity) on a schedule and records what it *would*
decide into the activity_verdicts table — without gating anything. This is the
safety rung before automation: we watch a few days of verdicts against reality
("did it call the house idle while someone was mid-stream?") and adjust the
thresholds before a single maintenance job is wired to them.

Reads are done through the oracle's own read-only path; only the verdict rows
are written (via connect_db). Mirrors the loop-service shape of
lantern.inventory_ingest.
"""

from __future__ import annotations

import argparse
import json
import time

from lantern.activity import is_homelab_idle, is_internet_idle
from lantern.common import DEFAULT_DB, connect_db

DEFAULT_INTERVAL = 300  # 5 min — well under the oracle's 30-min window

SIGNALS = {
    "internet": is_internet_idle,
    "homelab": is_homelab_idle,
}


def _headline(ev: dict) -> str:
    """The short metric behind the verdict, for at-a-glance scanning."""
    if "total_mb" in ev:
        return f"{ev['total_mb']} MB"
    if "total_real_reqs" in ev:
        return f"{ev['total_real_reqs']} real requests"
    return ""


def record_once(conn, now: int | None = None) -> list[dict]:
    """Evaluate every signal once and persist a verdict row for each."""
    now = int(time.time()) if now is None else now
    verdicts = []
    for name, fn in SIGNALS.items():
        idle, ev = fn(conn, now)
        conn.execute(
            """
            INSERT INTO activity_verdicts(
                ts_unix, signal, idle, headline, evidence_json, ingested_unix
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now, name, int(idle), _headline(ev), json.dumps(ev), int(time.time())),
        )
        verdicts.append(ev)
    conn.commit()
    return verdicts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database, default {DEFAULT_DB}")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                    help=f"seconds between evaluations, default {DEFAULT_INTERVAL}")
    ap.add_argument("--once", action="store_true", help="record one verdict set and exit")
    args = ap.parse_args()

    conn = connect_db(args.db)  # provisions activity_verdicts via common.SCHEMA
    while True:
        for ev in record_once(conn):
            print(ev["summary"], flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
