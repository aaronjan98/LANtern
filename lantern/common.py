#!/usr/bin/env python3
"""Shared LANtern Phase 1 helpers."""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Iterable


DEFAULT_DB = "/var/lib/lantern/lantern.db"
DEFAULT_OUI_PATHS = (
    "/usr/share/nmap/nmap-mac-prefixes",
    "/usr/share/misc/oui.txt",
    "/usr/share/ieee-data/oui.txt",
    "/var/lib/ieee-data/oui.txt",
)

MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.IGNORECASE)


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_unix INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    mac TEXT PRIMARY KEY,
    hostname TEXT,
    vendor TEXT,
    first_seen_unix INTEGER,
    last_seen_unix INTEGER,
    last_ip TEXT,
    lease_expires_unix INTEGER,
    client_id TEXT,
    source TEXT,
    updated_unix INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS lease_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_unix INTEGER NOT NULL,
    mac TEXT NOT NULL,
    ip TEXT NOT NULL,
    hostname TEXT,
    vendor TEXT,
    lease_expires_unix INTEGER,
    client_id TEXT,
    source TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lease_observations_mac_time
    ON lease_observations(mac, observed_unix);
CREATE INDEX IF NOT EXISTS idx_lease_observations_ip_time
    ON lease_observations(ip, observed_unix);

CREATE TABLE IF NOT EXISTS dns_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix INTEGER NOT NULL,
    ts TEXT NOT NULL,
    client_ip TEXT NOT NULL,
    qtype TEXT NOT NULL,
    domain TEXT NOT NULL,
    raw TEXT NOT NULL,
    ingested_unix INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dns_queries_time
    ON dns_queries(ts_unix);
CREATE INDEX IF NOT EXISTS idx_dns_queries_client_time
    ON dns_queries(client_ip, ts_unix);
CREATE INDEX IF NOT EXISTS idx_dns_queries_domain_time
    ON dns_queries(domain, ts_unix);

CREATE TABLE IF NOT EXISTS dns_resolver_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix INTEGER NOT NULL,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    domain TEXT,
    result TEXT,
    upstream TEXT,
    raw TEXT NOT NULL,
    ingested_unix INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dns_resolver_events_time
    ON dns_resolver_events(ts_unix);
CREATE INDEX IF NOT EXISTS idx_dns_resolver_events_domain_time
    ON dns_resolver_events(domain, ts_unix);

CREATE TABLE IF NOT EXISTS dhcp_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix INTEGER NOT NULL,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    iface TEXT,
    ip TEXT,
    mac TEXT,
    hostname TEXT,
    raw TEXT NOT NULL,
    ingested_unix INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dhcp_events_time
    ON dhcp_events(ts_unix);
CREATE INDEX IF NOT EXISTS idx_dhcp_events_mac_time
    ON dhcp_events(mac, ts_unix);

CREATE TABLE IF NOT EXISTS netflow_templates (
    exporter_ip TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    template_id INTEGER NOT NULL,
    fields_json TEXT NOT NULL,
    updated_unix INTEGER NOT NULL,
    PRIMARY KEY (exporter_ip, source_id, template_id)
);

CREATE TABLE IF NOT EXISTS netflow_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_unix INTEGER NOT NULL,
    exporter_ip TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    src_addr TEXT,
    dst_addr TEXT,
    next_hop TEXT,
    input_snmp INTEGER,
    output_snmp INTEGER,
    packets INTEGER,
    bytes INTEGER,
    first_switched_ms INTEGER,
    last_switched_ms INTEGER,
    first_unix INTEGER,
    last_unix INTEGER,
    src_port INTEGER,
    dst_port INTEGER,
    tcp_flags INTEGER,
    protocol INTEGER,
    tos INTEGER,
    src_as INTEGER,
    dst_as INTEGER,
    src_mask INTEGER,
    dst_mask INTEGER,
    direction INTEGER,
    sampler_id INTEGER,
    raw_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_netflow_records_received
    ON netflow_records(received_unix);
CREATE INDEX IF NOT EXISTS idx_netflow_records_src_time
    ON netflow_records(src_addr, received_unix);
CREATE INDEX IF NOT EXISTS idx_netflow_records_dst_time
    ON netflow_records(dst_addr, received_unix);
"""


def connect_db(path: str = DEFAULT_DB) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    _chmod_if_possible(db_path, 0o664)
    return conn


def _chmod_if_possible(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def get_metadata(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO metadata(key, value, updated_unix)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_unix = excluded.updated_unix
        """,
        (key, value, now),
    )


def normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    cleaned = mac.strip().lower().replace("-", ":")
    if MAC_RE.match(cleaned):
        return cleaned
    hex_only = re.sub(r"[^0-9a-fA-F]", "", mac)
    if len(hex_only) == 12:
        return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2)).lower()
    return None


def clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or value == "*":
        return None
    return value


def mac_prefix(mac: str) -> str:
    return mac.replace(":", "").upper()[:6]


def is_local_administered(mac: str) -> bool:
    first_octet = int(mac.split(":", 1)[0], 16)
    return bool(first_octet & 0x02)


def load_oui(paths: Iterable[str] = DEFAULT_OUI_PATHS) -> dict[str, str]:
    vendors: dict[str, str] = {}
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parsed = _parse_oui_line(line)
                if parsed:
                    prefix, vendor = parsed
                    vendors.setdefault(prefix, vendor)
    return vendors


def _parse_oui_line(line: str) -> tuple[str, str] | None:
    # nmap format: "AABBCC Vendor Name"
    if re.match(r"^[0-9A-Fa-f]{6}\s+", line):
        prefix, vendor = line.split(None, 1)
        return prefix.upper(), vendor.strip()

    # IEEE oui.txt format: "AA-BB-CC   (hex)        Vendor Name"
    match = re.match(r"^([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})\s+\(hex\)\s+(.+)$", line)
    if match:
        prefix = "".join(match.group(i) for i in range(1, 4)).upper()
        return prefix, match.group(4).strip()

    return None


def vendor_for_mac(mac: str, oui: dict[str, str]) -> str | None:
    normalized = normalize_mac(mac)
    if normalized is None:
        return None
    if is_local_administered(normalized):
        return "Locally administered"
    return oui.get(mac_prefix(normalized))


def upsert_device(
    conn: sqlite3.Connection,
    *,
    mac: str,
    ip: str | None = None,
    hostname: str | None = None,
    vendor: str | None = None,
    observed_unix: int | None = None,
    lease_expires_unix: int | None = None,
    client_id: str | None = None,
    source: str | None = None,
) -> None:
    mac = normalize_mac(mac) or mac
    observed_unix = observed_unix or int(time.time())
    existing = conn.execute(
        "SELECT * FROM devices WHERE mac = ?",
        (mac,),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO devices(
                mac, hostname, vendor, first_seen_unix, last_seen_unix, last_ip,
                lease_expires_unix, client_id, source, updated_unix
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mac,
                clean_value(hostname),
                clean_value(vendor),
                observed_unix,
                observed_unix,
                clean_value(ip),
                lease_expires_unix,
                clean_value(client_id),
                clean_value(source),
                int(time.time()),
            ),
        )
        return

    first_seen = existing["first_seen_unix"]
    if first_seen is None or observed_unix < first_seen:
        first_seen = observed_unix

    last_seen = existing["last_seen_unix"]
    if last_seen is None or observed_unix > last_seen:
        last_seen = observed_unix

    conn.execute(
        """
        UPDATE devices SET
            hostname = COALESCE(?, hostname),
            vendor = COALESCE(?, vendor),
            first_seen_unix = ?,
            last_seen_unix = ?,
            last_ip = COALESCE(?, last_ip),
            lease_expires_unix = COALESCE(?, lease_expires_unix),
            client_id = COALESCE(?, client_id),
            source = COALESCE(?, source),
            updated_unix = ?
        WHERE mac = ?
        """,
        (
            clean_value(hostname),
            clean_value(vendor),
            first_seen,
            last_seen,
            clean_value(ip),
            lease_expires_unix,
            clean_value(client_id),
            clean_value(source),
            int(time.time()),
            mac,
        ),
    )
