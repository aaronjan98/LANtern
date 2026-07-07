#!/usr/bin/env python3
"""LANtern activity oracle — read-only idle detection.

Answers two yes/no questions so maintenance jobs (the Deco WiFi reboot, the
SnapRAID sync) only run when nobody is using anything:

    is_internet_idle(conn)  -> is anyone browsing / streaming right now?
    is_homelab_idle(conn)   -> is anyone hitting my self-hosted services?

Both are *pure* and *read-only*: they open the SQLite DB with ``mode=ro`` and
only SELECT, so they can never block or corrupt the live collectors' writes.
Each returns ``(is_idle: bool, evidence: dict)`` — the evidence explains the
verdict in plain terms ("idle: 0.4 MB, 0 active devices over the last 60 min").

Thresholds here are not guesses: they were calibrated on 2026-07-06 against
~924k flow rows / 29 days of netflow_records. The reasoning behind every number
lives in the spec (lantern-idle-automation-spec.md §5) and the vault note
"LANtern activity oracle — calibrating idle-detection from NetFlow". The short
version is embedded as comments below so this file stands on its own.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime

DEFAULT_DB = "/var/lib/lantern/lantern.db"

# --- Network identities -----------------------------------------------------
# Our LAN. NetFlow gives us raw IPs; "is this endpoint one of ours?" is a
# prefix test.
LAN_LIKE = "10.0.50.%"

# Devices excluded from the "is a *person* browsing?" signal. If we counted
# these, the network would never look idle: sweetpea alone runs a persistent
# WireGuard tunnel + nightly multi-GB VPS backup pulls that dwarf any human.
# The line isn't human-vs-machine, it's attended-interaction vs unattended-
# sensor — which is why the TV/Roku/Tablo are NOT here (someone mid-stream
# must block a reboot).
INFRA_IPS = {
    "10.0.50.47",   # sweetpea (homelab server)
    "10.0.50.83",   # qwerty
    "10.0.50.213",  # sauron
    "10.0.50.1",    # router
}
IOT_IPS = {
    "10.0.50.131", "10.0.50.101", "10.0.50.159",  # Google Nest / Home Mini
    "10.0.50.105", "10.0.50.197",                 # Wyze cameras
    "10.0.50.205", "10.0.50.201",                 # Nest Labs
    "10.0.50.207",                                # Flume water meter
}
EXCLUDED_IPS = INFRA_IPS | IOT_IPS

# Homelab-service usage is read from the nginx reverse-proxy access log
# (ingested into http_requests), NOT from NetFlow: every service sits behind
# one nginx on sweetpea, so local + Tailscale + Cloudflare requests all funnel
# through it. NetFlow can't see this — local client<->server traffic is
# east-west switched and never reaches the router. See the vault note
# "LANtern — data-source vantage points".
#
# Background noise to exclude (the machines, not the people) — the nginx
# equivalent of the infra/IoT exclusion in the NetFlow pass:
HOMELAB_BACKGROUND_UA_LIKE = (
    "COOLWSD%",           # Collabra Online keepalive (Nextcloud office)
    "%Crawler%",          # Nextcloud-Server-Crawler etc.
    "Go-http-client%",    # service-to-service polling
    "python-requests%",
    "Prometheus%",
)
HOMELAB_SERVER_SIDE_IPS = {
    "10.0.50.83",   # qwerty — its own COOLWSD/crawler jobs originate here
    "172.18.0.1",   # docker bridge gateway (internal health/proxy hops)
}
# A single request to any of these = someone is actively streaming right now.
HOMELAB_STREAMING_PATH_LIKE = (
    "%/Sessions/Playing%",    # Jellyfin/Emby playback heartbeat (~every 10s)
    "%/videos/%", "%/Videos/%", "%/Audio/%",
    "%getTranscodeDecision%", "%/stream%",
)

# --- Calibrated thresholds (2026-07-06) -------------------------------------
MB = 1_000_000  # decimal MB; the ~4x dead-zone makes MB-vs-MiB immaterial

BUCKET_MIN = 30           # size of each measurement window
LOOKBACK_MIN = 60         # total lookback => 2 consecutive windows ("sustained")

# is_internet_idle: overnight idle floor measured 0.2-0.9 MB / 0 active devices;
# lightest real use jumps to 4-65 MB / 1-3 devices. 3 MB sits in the dead-zone.
INTERNET_MB_PER_WINDOW = 3            # human WAN bytes per 30-min window
INTERNET_ACTIVE_DEVICE_MB = 1        # a device moving > this counts as "active"

# is_homelab_idle: after excluding background, a truly quiet 30-min window has
# ~0 real user requests; browsing bursts a couple dozen. Any streaming-signature
# request means someone is watching/listening now. (First-draft; validate.)
HOMELAB_REAL_REQS_PER_WINDOW = 10   # non-streaming request count that means "in use"


def connect_ro(path: str = DEFAULT_DB) -> sqlite3.Connection:
    """Open the DB strictly read-only, so we can never disturb the writers."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _window_bounds(now: int) -> tuple[int, int, int]:
    """Return (start_unix, bucket_seconds, n_windows) for the lookback."""
    bucket_sec = BUCKET_MIN * 60
    n_windows = LOOKBACK_MIN // BUCKET_MIN
    start = now - n_windows * bucket_sec
    return start, bucket_sec, n_windows


def is_internet_idle(conn: sqlite3.Connection, now: int | None = None) -> tuple[bool, dict]:
    """True when no *person* is browsing/streaming over the last LOOKBACK_MIN.

    Signal: human client devices (LAN minus infra/IoT), WAN-facing, counting
    BOTH directions. NetFlow bytes are directional — a download lives in flows
    where dst_addr is the LAN device — so we take whichever side is local and
    sum per device per window. We bucket on received_unix (the collector's
    clock); the exporter's own timestamps are skewed ~1h.

    IDLE requires every window in the lookback to be quiet: < 3 MB human WAN
    bytes AND 0 devices that moved > 1 MB. Two windows must both pass, which
    rejects the isolated ~1.8 MB phone-sync blip that fires a couple times a
    night.
    """
    now = int(time.time()) if now is None else now
    start, bucket_sec, n_windows = _window_bounds(now)

    placeholders = ",".join("?" for _ in EXCLUDED_IPS)
    excluded = sorted(EXCLUDED_IPS)
    # lan_ip: the endpoint on our LAN, whichever side of the flow it's on.
    lan_ip = "CASE WHEN src_addr LIKE ? THEN src_addr ELSE dst_addr END"
    sql = f"""
        SELECT
            CAST((received_unix - ?) / ? AS INTEGER) AS win,
            {lan_ip} AS lan_ip,
            SUM(bytes) AS bytes
        FROM netflow_records
        WHERE received_unix >= ? AND received_unix < ?
          AND bytes IS NOT NULL
          -- WAN-facing: exactly one side is on the LAN (XOR of two 0/1 LIKEs)
          AND (src_addr LIKE ?) <> (dst_addr LIKE ?)
          AND ({lan_ip}) NOT IN ({placeholders})
        GROUP BY win, lan_ip
    """
    params = [start, bucket_sec, LAN_LIKE, start, now,
              LAN_LIKE, LAN_LIKE, LAN_LIKE, *excluded]
    rows = conn.execute(sql, params).fetchall()

    # Fold the per-(window, device) rows into per-window summaries.
    windows = [{"bytes": 0, "active_devices": 0, "top_ip": None, "top_bytes": 0}
               for _ in range(n_windows)]
    for row in rows:
        w = row["win"]
        if w < 0 or w >= n_windows:
            continue
        b = int(row["bytes"] or 0)
        win = windows[w]
        win["bytes"] += b
        if b > INTERNET_ACTIVE_DEVICE_MB * MB:
            win["active_devices"] += 1
        if b > win["top_bytes"]:
            win["top_bytes"] = b
            win["top_ip"] = row["lan_ip"]

    idle = all(
        w["bytes"] < INTERNET_MB_PER_WINDOW * MB and w["active_devices"] == 0
        for w in windows
    )
    evidence = _evidence(
        "internet", idle, now, start, bucket_sec, windows,
        thresholds={
            "bytes_per_window_mb": INTERNET_MB_PER_WINDOW,
            "active_device_mb": INTERNET_ACTIVE_DEVICE_MB,
        },
    )
    return idle, evidence


def is_homelab_idle(conn: sqlite3.Connection, now: int | None = None) -> tuple[bool, dict]:
    """True when nobody is using the self-hosted services over the lookback.

    Signal: nginx access log (table http_requests) — the one reverse proxy every
    service sits behind, so it sees local, Tailscale, and Cloudflare access alike.
    We drop background chatter (keepalive UAs + server-side source IPs), then a
    window is BUSY if anyone is streaming (a playback-signature request) or the
    real user-request count crosses the threshold.

    IDLE requires every window in the lookback to be non-busy.
    """
    now = int(time.time()) if now is None else now
    start, bucket_sec, n_windows = _window_bounds(now)

    ua_filter = " AND ".join("user_agent NOT LIKE ?" for _ in HOMELAB_BACKGROUND_UA_LIKE)
    ip_ph = ",".join("?" for _ in HOMELAB_SERVER_SIDE_IPS)
    stream_expr = " OR ".join("path LIKE ?" for _ in HOMELAB_STREAMING_PATH_LIKE)
    sql = f"""
        SELECT
            CAST((ts_unix - ?) / ? AS INTEGER) AS win,
            COUNT(*) AS reqs,
            SUM(CASE WHEN {stream_expr} THEN 1 ELSE 0 END) AS streaming
        FROM http_requests
        WHERE ts_unix >= ? AND ts_unix < ?
          AND (user_agent IS NULL OR ({ua_filter}))
          AND (client_ip IS NULL OR client_ip NOT IN ({ip_ph}))
        GROUP BY win
    """
    params = [start, bucket_sec, *HOMELAB_STREAMING_PATH_LIKE, start, now,
              *HOMELAB_BACKGROUND_UA_LIKE, *sorted(HOMELAB_SERVER_SIDE_IPS)]
    rows = conn.execute(sql, params).fetchall()

    windows = [{"real_reqs": 0, "streaming": 0} for _ in range(n_windows)]
    for row in rows:
        w = row["win"]
        if w < 0 or w >= n_windows:
            continue
        windows[w]["real_reqs"] = int(row["reqs"] or 0)
        windows[w]["streaming"] = int(row["streaming"] or 0)

    def busy(w):
        return w["streaming"] > 0 or w["real_reqs"] >= HOMELAB_REAL_REQS_PER_WINDOW

    idle = not any(busy(w) for w in windows)
    evidence = _evidence(
        "homelab", idle, now, start, bucket_sec, windows,
        thresholds={
            "real_reqs_per_window": HOMELAB_REAL_REQS_PER_WINDOW,
            "any_streaming_request": "busy",
        },
    )
    return idle, evidence


def _evidence(signal, idle, now, start, bucket_sec, windows, thresholds) -> dict:
    """Build a human-readable, machine-parseable explanation of the verdict.

    Neutral to the signal's shape: NetFlow windows carry ``bytes``; nginx
    windows carry ``real_reqs``/``streaming``. We render whichever is present.
    """
    detail = []
    for i, w in enumerate(windows):
        item = {"window_start": _ts(start + i * bucket_sec)}
        if "bytes" in w:
            item["mb"] = round(w["bytes"] / MB, 3)
            item.update({k: v for k, v in w.items() if k != "bytes"})
        else:
            item.update(w)
        detail.append(item)

    if windows and "bytes" in windows[0]:
        total = sum(w["bytes"] for w in windows)
        headline, total_key, total_val = f"{round(total / MB, 2)} MB", "total_mb", round(total / MB, 3)
    else:
        total = sum(w.get("real_reqs", 0) for w in windows)
        headline, total_key, total_val = f"{total} real requests", "total_real_reqs", total

    verdict = "idle" if idle else "busy"
    summary = (
        f"{signal} {verdict}: {headline} over "
        f"{len(windows)} x {BUCKET_MIN}-min windows ending {_ts(now)}"
    )
    return {
        "signal": signal,
        "idle": idle,
        "summary": summary,
        "as_of": _ts(now),
        "lookback_min": LOOKBACK_MIN,
        total_key: total_val,
        "thresholds": thresholds,
        "windows": detail,
    }


def _ts(value: int) -> str:
    return datetime.fromtimestamp(int(value)).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("signal", choices=["internet", "homelab", "all"],
                        nargs="?", default="all")
    parser.add_argument("--at-unix", type=int, default=None,
                        help="evaluate as if 'now' were this epoch (for testing history)")
    parser.add_argument("--json", action="store_true", help="emit raw evidence as JSON")
    args = parser.parse_args()

    conn = connect_ro(args.db)
    checks = []
    if args.signal in ("internet", "all"):
        checks.append(is_internet_idle(conn, args.at_unix))
    if args.signal in ("homelab", "all"):
        checks.append(is_homelab_idle(conn, args.at_unix))

    if args.json:
        print(json.dumps([ev for _, ev in checks], indent=2))
        return 0

    for _, ev in checks:
        print(ev["summary"])
        for w in ev["windows"]:
            parts = "  ".join(f"{k}={v}" for k, v in w.items() if k != "window_start")
            print(f"    {w['window_start']}  {parts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
