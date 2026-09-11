#!/usr/bin/env python3
"""Retention pruning for the LANtern DB — bounds otherwise-unbounded growth.

The collectors never delete, so the DB grows forever (4.3 GB after ~5 weeks; the
chatty tables are dns_resolver_events ~10.7M rows, dns_queries ~3.3M, netflow
~1.2M). This deletes rows past a per-table retention horizon, in small batches so
the WAL never balloons and other collectors keep writing between batches.

DELETE alone bounds growth (freed pages are reused by new inserts) but does not
shrink the file on disk. Pass --vacuum to reclaim to the OS — note VACUUM takes an
exclusive lock and rewrites the whole file, so run it deliberately, not on a tick.
"""
from __future__ import annotations

import argparse
import time

from lantern.common import DEFAULT_DB, connect_db

# table -> (time_column, retention_days). Small/high-value tables are kept long;
# the big chatty raw tables are trimmed hardest. Anything not listed is untouched
# (devices, kuma_monitors, metadata, netflow_templates, ...).
RETENTION: dict[str, tuple[str, int]] = {
    "dns_resolver_events": ("ts_unix", 14),
    "dns_queries":         ("ts_unix", 30),
    "netflow_records":     ("received_unix", 30),
    "http_requests":       ("ts_unix", 30),
    "dhcp_events":         ("ts_unix", 90),
    "wan_checks":          ("ts_unix", 90),
    "kuma_heartbeats":     ("ts_unix", 90),
    "lease_observations":  ("observed_unix", 180),
    "ssh_events":          ("ts_unix", 180),
    "fail2ban_events":     ("ts_unix", 180),
    "activity_verdicts":   ("ts_unix", 365),
    "speedtest_results":   ("ts_unix", 365),
}


def _count_old(conn, table, tcol, cutoff) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {tcol} < ?", (cutoff,)
    ).fetchone()[0]


def prune_table(conn, table, tcol, days, batch, dry) -> int:
    cutoff = int(time.time()) - days * 86400
    if dry:
        return _count_old(conn, table, tcol, cutoff)
    total = 0
    while True:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE rowid IN "
            f"(SELECT rowid FROM {table} WHERE {tcol} < ? LIMIT ?)",
            (cutoff, batch),
        )
        conn.commit()
        if cur.rowcount <= 0:
            break
        total += cur.rowcount
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    return total


def prune_devices(conn, days, dry) -> int:
    """Drop stale randomized-MAC device ghosts, and ONLY those.

    Prunes only **randomized (locally-administered) MACs** -- real-MAC devices are
    stable identities (printers, cameras, servers, IoT) and are always kept. Never
    touches anything you've claimed: assigned to a person, labeled, or reviewed (a
    known guest); nor static reservations; nor a currently-present device (live
    lease -> last_seen fresh). And a *named* randomized record is pruned only when
    its name belongs to a device you've already identified -- i.e. it's a
    superseded MAC-rotation of a known phone; a lone uniquely-named randomized
    device (a bridge/VM/server) is kept.
    """
    cutoff = int(time.time()) - days * 86400
    identified = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT hostname FROM devices WHERE hostname IS NOT NULL "
            "AND (person_id IS NOT NULL OR label IS NOT NULL)"
        )
        if row[0]
    }
    candidates = conn.execute(
        "SELECT mac, hostname FROM devices "
        "WHERE vendor = 'Locally administered' AND last_seen_unix < ? "
        "AND person_id IS NULL AND label IS NULL AND reviewed_unix IS NULL "
        "AND (source IS NULL OR source != 'dnsmasq-static')",
        (cutoff,),
    ).fetchall()
    to_delete = [
        mac for mac, host in candidates
        if not (host and host.strip() and host.strip() not in ("*", "localhost"))
        or host.strip() in identified
    ]
    if dry:
        return len(to_delete)
    for mac in to_delete:
        conn.execute("DELETE FROM devices WHERE mac = ?", (mac,))
    conn.commit()
    return len(to_delete)


def run_once(conn, batch, dry, do_vacuum, device_days) -> None:
    verb = "would delete" if dry else "deleted"
    grand = 0
    for table, (tcol, days) in RETENTION.items():
        try:
            n = prune_table(conn, table, tcol, days, batch, dry)
        except Exception as e:  # noqa: BLE001 -- a bad table shouldn't stop the rest
            print(f"[{int(time.time())}] {table}: ERROR {e}", flush=True)
            continue
        grand += n
        if n:
            print(f"[{int(time.time())}] {table}: {verb} {n} rows (keep {days}d)", flush=True)
    try:
        n = prune_devices(conn, device_days, dry)
        grand += n
        if n:
            print(f"[{int(time.time())}] devices: {verb} {n} stale records "
                  f"(unlabeled/unassigned, unseen {device_days}d)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[{int(time.time())}] devices: ERROR {e}", flush=True)
    print(f"[{int(time.time())}] total {verb}: {grand} rows", flush=True)
    if dry:
        return
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    if do_vacuum:
        print(f"[{int(time.time())}] VACUUM (exclusive lock, rewriting file)...", flush=True)
        conn.execute("VACUUM")
        print(f"[{int(time.time())}] VACUUM done", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--interval", type=float, default=86400, help="seconds between runs (default daily)")
    ap.add_argument("--batch", type=int, default=20000, help="rows per delete batch")
    ap.add_argument("--dry-run", action="store_true", help="report what would be deleted, change nothing")
    ap.add_argument("--vacuum", action="store_true", help="VACUUM after pruning (reclaims disk; locks the DB)")
    ap.add_argument("--device-retention-days", type=int, default=14,
                    help="drop unlabeled/unassigned device records unseen this long (default 14)")
    ap.add_argument("--once", action="store_true", help="run one pass and exit")
    args = ap.parse_args()

    conn = connect_db(args.db)
    while True:
        run_once(conn, args.batch, args.dry_run, args.vacuum, args.device_retention_days)
        if args.once or args.dry_run:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
