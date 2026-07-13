#!/usr/bin/env python3
"""Fold uptime-kuma's monitor history into LANtern (kuma_monitors, kuma_heartbeats).

uptime-kuma already probes ~20 services plus WAN pings (router, public IP) and
stores every heartbeat in its own kuma.db. Copying that history into LANtern lets
an agent correlate a service or WAN outage with DNS, flows and device activity
("was Jellyfin actually down when Michelle couldn't connect?").

kuma.db is a live WAL database owned by another container, so we never open it in
place: each cycle we copy kuma.db(+wal/shm) to a temp dir and read the copy. New
heartbeats past the last ingested kuma id are appended; monitors are upserted.
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

from lantern.common import DEFAULT_DB, connect_db, get_metadata, set_metadata

MAXID_KEY = "kuma.heartbeat.max_id"


def _snapshot(kuma_dir: str) -> str:
    """Copy the live kuma db (+wal/shm) to a private temp dir; return the path."""
    tmp = tempfile.mkdtemp(prefix="kuma-")
    for f in glob.glob(os.path.join(kuma_dir, "kuma.db*")):
        shutil.copy2(f, tmp)
    return os.path.join(tmp, "kuma.db")


def _parse_ts(s: str | None) -> int | None:
    """kuma stores heartbeat time as a UTC string 'YYYY-MM-DD HH:MM:SS(.fff)'."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def sync_monitors(src, dst) -> int:
    rows = src.execute(
        "SELECT id, name, type, url, hostname, port, active FROM monitor"
    ).fetchall()
    now = int(time.time())
    for r in rows:
        dst.execute(
            """
            INSERT INTO kuma_monitors (id, name, type, url, hostname, port, active, updated_unix)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name, type=excluded.type, url=excluded.url,
              hostname=excluded.hostname, port=excluded.port,
              active=excluded.active, updated_unix=excluded.updated_unix
            """,
            (r["id"], r["name"], r["type"], r["url"], r["hostname"], r["port"], r["active"], now),
        )
    dst.commit()
    return len(rows)


def sync_heartbeats(src, dst, batch: int) -> int:
    saved = get_metadata(dst, MAXID_KEY)
    last_id = int(saved) if saved is not None else 0
    now = int(time.time())
    total = 0
    while True:
        rows = src.execute(
            "SELECT id, monitor_id, status, msg, time, ping, important "
            "FROM heartbeat WHERE id > ? ORDER BY id ASC LIMIT ?",
            (last_id, batch),
        ).fetchall()
        if not rows:
            break
        for r in rows:
            dst.execute(
                """
                INSERT OR IGNORE INTO kuma_heartbeats
                    (kuma_id, monitor_id, status, ts_unix, ping_ms, important, msg, ingested_unix)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (r["id"], r["monitor_id"], r["status"], _parse_ts(r["time"]),
                 r["ping"], r["important"], r["msg"], now),
            )
            last_id = r["id"]
        dst.commit()
        set_metadata(dst, MAXID_KEY, str(last_id))
        dst.commit()
        total += len(rows)
        if len(rows) < batch:
            break
    return total


def one_cycle(kuma_dir: str, dst, batch: int):
    db = _snapshot(kuma_dir)
    tmpdir = os.path.dirname(db)
    try:
        src = sqlite3.connect(db)
        src.row_factory = sqlite3.Row
        m = sync_monitors(src, dst)
        h = sync_heartbeats(src, dst, batch)
        src.close()
        return m, h
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--kuma-dir", default="/kuma")
    ap.add_argument("--interval", type=float, default=60)
    ap.add_argument("--batch", type=int, default=5000)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    dst = connect_db(args.db)  # provisions kuma_* via SCHEMA
    while True:
        m, h = one_cycle(args.kuma_dir, dst, args.batch)
        print(f"[{int(time.time())}] kuma sync: {m} monitors, +{h} heartbeats", flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
