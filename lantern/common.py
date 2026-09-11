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
    label TEXT,
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

-- Reverse-proxy access log (LANtern's service-usage vantage point). Every
-- hosted service sits behind one nginx on sweetpea, so this captures local,
-- Tailscale, and Cloudflare access alike -- traffic NetFlow can't see because
-- local client<->server flows are east-west switched. Fed by
-- lantern.nginx_ingest, read by is_homelab_idle in lantern.activity.
CREATE TABLE IF NOT EXISTS http_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix INTEGER NOT NULL,
    client_ip TEXT,
    method TEXT,
    path TEXT,
    status INTEGER,
    bytes INTEGER,
    user_agent TEXT,
    referer TEXT,
    x_forwarded_for TEXT,
    raw TEXT NOT NULL,
    ingested_unix INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_http_requests_time ON http_requests(ts_unix);

-- Shadow-mode log of the activity oracle's verdicts (lantern.activity_shadow).
-- Records what the oracle *would* decide, without acting, so thresholds can be
-- validated against real usage before any maintenance job is gated on them.
CREATE TABLE IF NOT EXISTS activity_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix INTEGER NOT NULL,
    signal TEXT NOT NULL,          -- 'internet' | 'homelab'
    idle INTEGER NOT NULL,         -- 1 = idle, 0 = busy
    headline TEXT,                 -- e.g. '0.42 MB' or '3 real requests'
    evidence_json TEXT NOT NULL,   -- full evidence dict from lantern.activity
    ingested_unix INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_verdicts_time
    ON activity_verdicts(ts_unix);
CREATE INDEX IF NOT EXISTS idx_activity_verdicts_signal_time
    ON activity_verdicts(signal, ts_unix);

-- Security vantage point: SSH auth events (from /var/log/auth.log) and fail2ban
-- ban/unban actions (from /var/log/fail2ban.log), fed by lantern.security_ingest.
-- Together they answer "who is trying to get in, who got in, and is fail2ban
-- actually catching the attackers?" `host` scopes to a machine for future
-- multi-host ingest (v1 is sweetpea only).
CREATE TABLE IF NOT EXISTS ssh_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix INTEGER NOT NULL,
    event TEXT NOT NULL,           -- accepted | failed | invalid | preauth
    username TEXT,
    src_ip TEXT,
    port INTEGER,
    method TEXT,                   -- publickey | password | ...
    host TEXT,
    raw TEXT NOT NULL,
    ingested_unix INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ssh_events_time ON ssh_events(ts_unix);
CREATE INDEX IF NOT EXISTS idx_ssh_events_srcip_time ON ssh_events(src_ip, ts_unix);
CREATE INDEX IF NOT EXISTS idx_ssh_events_event_time ON ssh_events(event, ts_unix);

CREATE TABLE IF NOT EXISTS fail2ban_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix INTEGER NOT NULL,
    jail TEXT NOT NULL,
    action TEXT NOT NULL,          -- Found | Ban | Unban | Increase Ban | Restore Ban
    ip TEXT,
    host TEXT,
    raw TEXT NOT NULL,
    ingested_unix INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fail2ban_events_time ON fail2ban_events(ts_unix);
CREATE INDEX IF NOT EXISTS idx_fail2ban_events_ip_time ON fail2ban_events(ip, ts_unix);

-- Internet health: one row per scheduled Ookla speedtest (lantern.speedtest_ingest).
-- ok=0 rows record a failed run (e.g. the WAN was down) so gaps are explicit.
CREATE TABLE IF NOT EXISTS speedtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix INTEGER NOT NULL,
    ok INTEGER NOT NULL,             -- 1 = completed, 0 = failed
    download_bps REAL,               -- bits/second
    upload_bps REAL,                 -- bits/second
    ping_ms REAL,
    jitter_ms REAL,
    packet_loss REAL,                -- percent
    server_name TEXT,
    server_id INTEGER,
    isp TEXT,
    result_url TEXT,
    error TEXT,
    ingested_unix INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_speedtest_results_time ON speedtest_results(ts_unix);

-- WAN reachability probe every ~30s (lantern.wan_ingest). up=0 with
-- gateway_up=1 is the 'connected but no internet' case; outage windows are
-- derived from these rows at query time.
CREATE TABLE IF NOT EXISTS wan_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_unix INTEGER NOT NULL,
    up INTEGER NOT NULL,             -- 1 = internet reachable, 0 = down
    gateway_up INTEGER,              -- 1/0/NULL LAN gateway reachable
    rtt_ms REAL,                     -- best RTT of successful probes
    targets_ok INTEGER,
    targets_total INTEGER,
    detail TEXT,                     -- json per-target results
    ingested_unix INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wan_checks_time ON wan_checks(ts_unix);

-- Folded-in uptime-kuma history (lantern.kuma_ingest). Lets service/WAN
-- outages correlate with DNS/flows/device activity. kuma monitor+heartbeat
-- ids are preserved so re-ingest is idempotent.
CREATE TABLE IF NOT EXISTS kuma_monitors (
    id INTEGER PRIMARY KEY,          -- kuma monitor id
    name TEXT,
    type TEXT,
    url TEXT,
    hostname TEXT,
    port INTEGER,
    active INTEGER,
    updated_unix INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS kuma_heartbeats (
    kuma_id INTEGER PRIMARY KEY,     -- kuma heartbeat id (dedupe)
    monitor_id INTEGER NOT NULL,
    status INTEGER NOT NULL,         -- 0 down, 1 up, 2 pending, 3 maintenance
    ts_unix INTEGER,
    ping_ms REAL,
    important INTEGER,               -- 1 = state-change heartbeat
    msg TEXT,
    ingested_unix INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kuma_hb_monitor_time ON kuma_heartbeats(monitor_id, ts_unix);
CREATE INDEX IF NOT EXISTS idx_kuma_hb_time ON kuma_heartbeats(ts_unix);
CREATE INDEX IF NOT EXISTS idx_kuma_hb_important ON kuma_heartbeats(important, ts_unix);

-- Device->person identity: a person owns many devices (devices.person_id).
-- Shared/infrastructure devices stay unassigned (NULL). Lets LANtern and the
-- voice assistant answer 'who (a person) is online' rather than just 'what'.
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    note TEXT,
    created_unix INTEGER NOT NULL,
    updated_unix INTEGER NOT NULL
);

-- Deco WiFi reboot automation: state machine + audit log (lantern.deco_reboot
-- brain, the host runner, and the /api/reboot endpoints). The recurring Deco
-- X60 5 GHz uptime-degradation fault is only cleared by a power-cycle, so this
-- both schedules the mitigation and records every run. Lifecycle:
--   pending -> approved -> ready -> executing -> succeeded | failed
--   (or canceled / superseded).
-- An 'approved' backstop/manual reboot waits until the activity oracle reports
-- safe_for_deco_reboot (internet_idle && homelab_idle); the brain then flips it
-- to 'ready'. The host runner (it owns the Kasa plug; the containers can't) is
-- the only actor that executes -- it records the result plus an external
-- reachability check just before and after, so we can prove the Cloudflare
-- tunnel / hosted services never blipped when only the WiFi AP was cycled.
CREATE TABLE IF NOT EXISTS deco_reboot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_unix INTEGER NOT NULL,
    trigger TEXT NOT NULL,            -- scheduled_backstop | manual | degradation
    status TEXT NOT NULL,            -- pending|approved|ready|executing|succeeded|failed|canceled|superseded
    requested_by TEXT,               -- who created it (X-Forwarded-User) or 'scheduler'
    approved_by TEXT,                -- who approved, or 'scheduler' for the backstop
    approved_unix INTEGER,
    scheduled_unix INTEGER,          -- suggested/confirmed run time (overnight slot)
    ready_unix INTEGER,              -- when the idle gate opened
    executed_unix INTEGER,           -- when the host runner began the power-cycle
    finished_unix INTEGER,
    result TEXT,                     -- succeeded | failed
    idle_evidence_json TEXT,         -- oracle evidence captured at ready time
    tunnel_before TEXT,              -- external-reachability check just before
    tunnel_after TEXT,               -- ... and just after
    detail TEXT,                     -- runner notes / error / log tail
    updated_unix INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deco_reboot_events_status
    ON deco_reboot_events(status, created_unix);
CREATE INDEX IF NOT EXISTS idx_deco_reboot_events_created
    ON deco_reboot_events(created_unix);

-- Tailscale peer presence (lantern.tailscale_ingest -- ONE LocalAPI /status read
-- per interval, no per-device pings). Lets the People page show remote (Tailscale)
-- presence alongside on-LAN presence. Keyed by the stable 100.x tailnet IP;
-- `online` is the live tailnet state.
CREATE TABLE IF NOT EXISTS tailscale_peers (
    ts_ip TEXT PRIMARY KEY,          -- 100.x tailnet IP (stable device id)
    dns_name TEXT,                   -- MagicDNS short name, e.g. galaxy-zflip-aj (display)
    hostname TEXT,                   -- device self-reported host (phones report 'localhost')
    os TEXT,
    online INTEGER NOT NULL,         -- 1 = tailnet-online right now
    cur_addr TEXT,                   -- current direct endpoint ('' when relaying via DERP)
    relay TEXT,                      -- DERP region when relayed
    last_seen_unix INTEGER,
    updated_unix INTEGER NOT NULL
);
"""


def _migrate(conn):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(devices)")}
    if "label" not in existing:
        conn.execute("ALTER TABLE devices ADD COLUMN label TEXT")
    if "person_id" not in existing:
        conn.execute("ALTER TABLE devices ADD COLUMN person_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_devices_person ON devices(person_id)"
    )
    reboot_cols = {row[1] for row in conn.execute("PRAGMA table_info(deco_reboot_events)")}
    if reboot_cols and "scheduled_unix" not in reboot_cols:
        conn.execute("ALTER TABLE deco_reboot_events ADD COLUMN scheduled_unix INTEGER")
    people_cols = {row[1] for row in conn.execute("PRAGMA table_info(people)")}
    if people_cols:
        if "tailscale_ip" not in people_cols:  # person -> their tailnet device (100.x)
            conn.execute("ALTER TABLE people ADD COLUMN tailscale_ip TEXT")
        if "is_guest" not in people_cols:  # guests only render when currently present
            conn.execute("ALTER TABLE people ADD COLUMN is_guest INTEGER NOT NULL DEFAULT 0")
        if "lan_hostname" not in people_cols:  # stable DHCP name -> survives MAC rotation
            conn.execute("ALTER TABLE people ADD COLUMN lan_hostname TEXT")


def connect_db(path: str = DEFAULT_DB) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
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
