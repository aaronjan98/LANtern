#!/usr/bin/env python3
"""Sync dnsmasq leases and static host entries into the LANtern device inventory."""

from __future__ import annotations

import argparse
import re
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
DEFAULT_DHCP_CONF = "/etc/dnsmasq.d/dhcp.conf"

# dhcp-host=MAC,IP,hostname  (comment stripped, optional fields)
DHCP_HOST_RE = re.compile(
    r"^\s*dhcp-host\s*=\s*"
    r"(?P<mac>[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})"
    r",(?P<ip>\d{1,3}(?:\.\d{1,3}){3})"
    r"(?:,(?P<hostname>[^#\s]+))?",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leases", default=DEFAULT_LEASES)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--dhcp-conf", default=None,
                        help="dnsmasq config file with dhcp-host= static entries")
    parser.add_argument("--oui", action="append", default=[])
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
                (observed_unix, lease["mac"], lease["ip"], lease["hostname"],
                 vendor, lease["lease_expires_unix"], lease["client_id"], "dnsmasq-leases"),
            )
            upsert_device(conn, mac=str(lease["mac"]), ip=str(lease["ip"]),
                          hostname=lease["hostname"], vendor=vendor,
                          observed_unix=observed_unix,
                          lease_expires_unix=int(lease["lease_expires_unix"]),
                          client_id=lease["client_id"], source="dnsmasq-leases")
            count += 1
    conn.commit()
    return count


def ingest_static_hosts(conn, conf_path: Path, oui: dict[str, str]) -> int:
    """Seed devices from dhcp-host= lines in dnsmasq config (static IPs that never DHCP)."""
    observed_unix = int(time.time())
    count = 0
    with conf_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            # strip inline comments
            line = line.split("#")[0]
            m = DHCP_HOST_RE.match(line)
            if not m:
                continue
            mac = normalize_mac(m.group("mac"))
            if mac is None:
                continue
            ip = m.group("ip")
            hostname = clean_value(m.group("hostname"))
            vendor = vendor_for_mac(mac, oui)
            upsert_device(conn, mac=mac, ip=ip, hostname=hostname, vendor=vendor,
                          observed_unix=observed_unix, source="dnsmasq-static")
            count += 1
    conn.commit()
    return count


def main() -> int:
    args = parse_args()
    oui_paths = args.oui if args.oui else DEFAULT_OUI_PATHS
    oui = load_oui(oui_paths)
    conn = connect_db(args.db)

    lease_count = ingest_leases(conn, Path(args.leases), oui)
    print(f"ingested {lease_count} active leases from {args.leases}")

    if args.dhcp_conf:
        conf_path = Path(args.dhcp_conf)
        if conf_path.exists():
            static_count = ingest_static_hosts(conn, conf_path, oui)
            print(f"seeded {static_count} static hosts from {args.dhcp_conf}")
        else:
            print(f"dhcp-conf not found: {args.dhcp_conf}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
