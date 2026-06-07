#!/usr/bin/env python3
"""Sync dnsmasq leases into the LANtern device inventory."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from lantern.common import (
    DEFAULT_DB,
    DEFAULT_OUI_PATHS,
    clean_value,
    connect_db,
    load_oui,
    normalize_mac,
    upsert_device,
    vendor_for_mac,
)


DEFAULT_LEASES = "/var/lib/misc/dnsmasq.leases"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leases", default=DEFAULT_LEASES, help=f"dnsmasq lease file, default {DEFAULT_LEASES}")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path, default {DEFAULT_DB}")
    parser.add_argument(
        "--oui",
        action="append",
        default=[],
        help="OUI database path. Can be supplied multiple times. Defaults to known system paths.",
    )
    return parser.parse_args()


def parse_lease_line(line: str) -> dict[str, object] | None:
    parts = line.split()
    if len(parts) < 5:
        return None

    try:
        lease_expires_unix = int(parts[0])
    except ValueError:
        return None

    mac = normalize_mac(parts[1])
    if mac is None:
        return None

    return {
        "lease_expires_unix": lease_expires_unix,
        "mac": mac,
        "ip": parts[2],
        "hostname": clean_value(parts[3]),
        "client_id": clean_value(" ".join(parts[4:])),
    }


def ingest_leases(conn, leases_path: Path, oui: dict[str, str]) -> int:
    observed_unix = int(time.time())
    count = 0

    with leases_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            lease = parse_lease_line(line)
            if lease is None:
                continue

            vendor = vendor_for_mac(str(lease["mac"]), oui)
            conn.execute(
                """
                INSERT INTO lease_observations(
                    observed_unix, mac, ip, hostname, vendor, lease_expires_unix, client_id, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_unix,
                    lease["mac"],
                    lease["ip"],
                    lease["hostname"],
                    vendor,
                    lease["lease_expires_unix"],
                    lease["client_id"],
                    "dnsmasq-leases",
                ),
            )
            upsert_device(
                conn,
                mac=str(lease["mac"]),
                ip=str(lease["ip"]),
                hostname=lease["hostname"],
                vendor=vendor,
                observed_unix=observed_unix,
                lease_expires_unix=int(lease["lease_expires_unix"]),
                client_id=lease["client_id"],
                source="dnsmasq-leases",
            )
            count += 1

    conn.commit()
    return count


def main() -> int:
    args = parse_args()
    leases_path = Path(args.leases)
    oui_paths = args.oui if args.oui else DEFAULT_OUI_PATHS
    oui = load_oui(oui_paths)
    conn = connect_db(args.db)
    count = ingest_leases(conn, leases_path, oui)
    print(f"ingested {count} active leases from {leases_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
