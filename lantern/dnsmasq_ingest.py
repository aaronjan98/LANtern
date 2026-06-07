#!/usr/bin/env python3
"""Tail dnsmasq logs into the LANtern SQLite database."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from lantern.common import DEFAULT_DB, connect_db, get_metadata, normalize_mac, set_metadata, upsert_device


DEFAULT_LOG = "/var/log/dnsmasq.log"
OFFSET_KEY = "dnsmasq.log.offset"

SYSLOG_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]{2})\s+"
    r"(?P<day>\d{1,2})\s+"
    r"(?P<clock>\d{2}:\d{2}:\d{2})\s+"
    r"(?P<process>[A-Za-z0-9_-]+)\[(?P<pid>\d+)\]:\s+"
    r"(?P<message>.*)$"
)
QUERY_RE = re.compile(r"^query\[(?P<qtype>[^\]]+)\]\s+(?P<domain>\S+)\s+from\s+(?P<client_ip>\S+)$")
FORWARDED_RE = re.compile(r"^forwarded\s+(?P<domain>\S+)\s+to\s+(?P<upstream>\S+)$")
RESULT_RE = re.compile(r"^(?P<action>reply|cached|config)\s+(?P<domain>\S+)\s+is\s+(?P<result>.+)$")
DHCP_RE = re.compile(
    r"^(?:(?P<txid>\d+)\s+)?"
    r"(?P<action>DHCP[A-Z]+)"
    r"(?:\((?P<iface>[^)]+)\))?"
    r"(?:\s+(?P<ip>\S+))?"
    r"(?:\s+(?P<mac>[0-9a-fA-F:]{17}))?"
    r"(?:\s+(?P<hostname>\S+))?"
    r"\s*$"
)
DHCP_HOST_RE = re.compile(r"^DHCP\s+(?P<ip>\S+)\s+is\s+(?P<hostname>\S+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default=DEFAULT_LOG, help=f"dnsmasq log path, default {DEFAULT_LOG}")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path, default {DEFAULT_DB}")
    parser.add_argument("--poll", type=float, default=1.0, help="poll interval in seconds when following")
    parser.add_argument("--once", action="store_true", help="ingest currently available lines and exit")
    parser.add_argument("--from-end", action="store_true", help="start at end if no saved offset exists")
    parser.add_argument("--state-key", default=OFFSET_KEY, help="metadata key used to store log offset")
    return parser.parse_args()


def parse_syslog_time(month: str, day: str, clock: str) -> tuple[int, str]:
    now = datetime.now().astimezone()
    parsed = datetime.strptime(f"{now.year} {month} {day} {clock}", "%Y %b %d %H:%M:%S")
    parsed = parsed.replace(tzinfo=now.tzinfo)
    if parsed - now > timedelta(days=1):
        parsed = parsed.replace(year=parsed.year - 1)
    return int(parsed.timestamp()), parsed.isoformat()


def parse_line(line: str) -> dict[str, object] | None:
    raw = line.rstrip("\n")
    match = SYSLOG_RE.match(raw)
    if not match:
        return None

    ts_unix, ts = parse_syslog_time(match.group("month"), match.group("day"), match.group("clock"))
    message = match.group("message")
    process = match.group("process")

    query = QUERY_RE.match(message)
    if query:
        return {
            "kind": "dns_query",
            "ts_unix": ts_unix,
            "ts": ts,
            "client_ip": query.group("client_ip"),
            "qtype": query.group("qtype"),
            "domain": query.group("domain").rstrip(".").lower(),
            "raw": raw,
        }

    forwarded = FORWARDED_RE.match(message)
    if forwarded:
        return {
            "kind": "dns_resolver",
            "ts_unix": ts_unix,
            "ts": ts,
            "action": "forwarded",
            "domain": forwarded.group("domain").rstrip(".").lower(),
            "result": None,
            "upstream": forwarded.group("upstream"),
            "raw": raw,
        }

    result = RESULT_RE.match(message)
    if result:
        return {
            "kind": "dns_resolver",
            "ts_unix": ts_unix,
            "ts": ts,
            "action": result.group("action"),
            "domain": result.group("domain").rstrip(".").lower(),
            "result": result.group("result"),
            "upstream": None,
            "raw": raw,
        }

    if process == "dnsmasq-dhcp":
        dhcp = DHCP_RE.match(message)
        if dhcp:
            return {
                "kind": "dhcp",
                "ts_unix": ts_unix,
                "ts": ts,
                "action": dhcp.group("action"),
                "iface": dhcp.group("iface"),
                "ip": dhcp.group("ip"),
                "mac": normalize_mac(dhcp.group("mac")),
                "hostname": clean_hostname(dhcp.group("hostname")),
                "raw": raw,
            }

    dhcp_host = DHCP_HOST_RE.match(message)
    if dhcp_host:
        return {
            "kind": "dhcp",
            "ts_unix": ts_unix,
            "ts": ts,
            "action": "DHCPHOST",
            "iface": None,
            "ip": dhcp_host.group("ip"),
            "mac": None,
            "hostname": clean_hostname(dhcp_host.group("hostname")),
            "raw": raw,
        }

    return None


def clean_hostname(hostname: str | None) -> str | None:
    if not hostname or hostname == "*":
        return None
    return hostname


def insert_event(conn, event: dict[str, object]) -> None:
    ingested = int(time.time())
    kind = event["kind"]

    if kind == "dns_query":
        conn.execute(
            """
            INSERT INTO dns_queries(ts_unix, ts, client_ip, qtype, domain, raw, ingested_unix)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["ts_unix"],
                event["ts"],
                event["client_ip"],
                event["qtype"],
                event["domain"],
                event["raw"],
                ingested,
            ),
        )
        return

    if kind == "dns_resolver":
        conn.execute(
            """
            INSERT INTO dns_resolver_events(ts_unix, ts, action, domain, result, upstream, raw, ingested_unix)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["ts_unix"],
                event["ts"],
                event["action"],
                event["domain"],
                event["result"],
                event["upstream"],
                event["raw"],
                ingested,
            ),
        )
        return

    if kind == "dhcp":
        conn.execute(
            """
            INSERT INTO dhcp_events(ts_unix, ts, action, iface, ip, mac, hostname, raw, ingested_unix)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["ts_unix"],
                event["ts"],
                event["action"],
                event["iface"],
                event["ip"],
                event["mac"],
                event["hostname"],
                event["raw"],
                ingested,
            ),
        )
        if event.get("mac"):
            upsert_device(
                conn,
                mac=str(event["mac"]),
                ip=event.get("ip"),
                hostname=event.get("hostname"),
                observed_unix=int(event["ts_unix"]),
                source="dnsmasq-log",
            )
        return


def initial_offset(conn, log_path: Path, state_key: str, from_end: bool) -> int:
    saved = get_metadata(conn, state_key)
    if saved is not None:
        try:
            return int(saved)
        except ValueError:
            return 0
    return log_path.stat().st_size if from_end and log_path.exists() else 0


def ingest_available(conn, log_path: Path, state_key: str, offset: int) -> tuple[int, int]:
    if not log_path.exists():
        raise FileNotFoundError(log_path)

    size = log_path.stat().st_size
    if offset > size:
        offset = 0

    parsed = 0
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        for line in handle:
            event = parse_line(line)
            if event:
                insert_event(conn, event)
                parsed += 1
        offset = handle.tell()

    set_metadata(conn, state_key, str(offset))
    conn.commit()
    return offset, parsed


def main() -> int:
    args = parse_args()
    log_path = Path(args.log)
    conn = connect_db(args.db)
    offset = initial_offset(conn, log_path, args.state_key, args.from_end)

    while True:
        try:
            offset, parsed = ingest_available(conn, log_path, args.state_key, offset)
            if parsed:
                print(f"ingested {parsed} dnsmasq events through offset {offset}", flush=True)
        except PermissionError as exc:
            print(f"permission denied reading {log_path}: {exc}", file=sys.stderr, flush=True)
            return 13
        except FileNotFoundError:
            print(f"waiting for {log_path}", file=sys.stderr, flush=True)

        if args.once:
            break
        time.sleep(args.poll)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
