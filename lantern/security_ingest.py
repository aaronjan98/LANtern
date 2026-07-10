#!/usr/bin/env python3
"""Tail SSH auth + fail2ban logs into LANtern (ssh_events, fail2ban_events).

LANtern's *security* vantage point: who logs in, who only *tries* to, and what
fail2ban does about it. sshd auth events come from /var/log/auth.log; fail2ban
ban/unban actions from /var/log/fail2ban.log. Both are tailed exactly like
lantern.nginx_ingest -- a byte offset per file in the metadata table, so it
resumes across restarts without re-ingesting.

Putting the attack attempts and the bans in one place answers the question that
started this: "is fail2ban actually working?" (In 2026-06 it silently wasn't --
logrotate had wedged rsyslog and 2,109 brute-force attempts went uncounted.)
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

from lantern.common import DEFAULT_DB, connect_db, get_metadata, set_metadata

DEFAULT_AUTH_LOG = "/data/auth.log"
DEFAULT_FAIL2BAN_LOG = "/data/fail2ban.log"
AUTH_OFFSET_KEY = "security.auth.offset"
F2B_OFFSET_KEY = "security.fail2ban.offset"

# --- auth.log (sshd) --------------------------------------------------------
# Traditional syslog: "Jul 10 18:33:21 sweetpea sshd[191939]: <msg>"
_SYSLOG_RE = re.compile(
    r"^(?P<mon>\w{3})\s+(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2}:\d{2})\s+"
    r"\S+\s+sshd\[\d+\]:\s+(?P<msg>.*)$"
)
# Message patterns, tried in order. First match wins.
_MSG_RES = (
    ("accepted", re.compile(r"^Accepted (?P<method>\S+) for (?P<user>\S+) from (?P<ip>\S+) port (?P<port>\d+)")),
    ("failed",   re.compile(r"^Failed (?P<method>\S+) for (?:invalid user )?(?P<user>\S+) from (?P<ip>\S+) port (?P<port>\d+)")),
    ("invalid",  re.compile(r"^Invalid user (?P<user>\S+) from (?P<ip>\S+) port (?P<port>\d+)")),
    ("preauth",  re.compile(r"^Connection closed by (?:authenticating |invalid )?user (?P<user>\S+) (?P<ip>\S+) port (?P<port>\d+)")),
)


def _syslog_ts(mon: str, day: str, hms: str, now: float | None = None) -> int | None:
    """Traditional syslog carries no year; infer it, handling the Dec->Jan wrap."""
    now = time.time() if now is None else now
    try:
        dt = datetime.strptime(f"{mon} {int(day):02d} {hms}", "%b %d %H:%M:%S")
    except ValueError:
        return None
    year = datetime.fromtimestamp(now).year
    dt = dt.replace(year=year)
    ts = dt.timestamp()
    if ts - now > 86400:  # a Dec line read in early Jan belongs to last year
        ts = dt.replace(year=year - 1).timestamp()
    return int(ts)


def parse_auth(line: str) -> dict | None:
    m = _SYSLOG_RE.match(line.rstrip("\n"))
    if not m:
        return None
    msg = m.group("msg")
    for event, rx in _MSG_RES:
        mm = rx.match(msg)
        if not mm:
            continue
        g = mm.groupdict()
        ts = _syslog_ts(m.group("mon"), m.group("day"), m.group("time"))
        if ts is None:
            return None
        return {
            "ts_unix": ts,
            "event": event,
            "username": g.get("user"),
            "src_ip": g.get("ip"),
            "port": int(g["port"]) if g.get("port") else None,
            "method": g.get("method"),
            "raw": line.rstrip("\n"),
        }
    return None


# --- fail2ban.log -----------------------------------------------------------
# "2026-07-10 17:55:19,067 fail2ban.actions [895]: NOTICE [sshd] Ban 1.2.3.4"
_F2B_RE = re.compile(
    r"^(?P<t>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]\d+\s.*"
    r"\[(?P<jail>[^\]]+)\]\s+(?P<action>Ban|Unban|Found|Increase Ban|Restore Ban)\s+"
    r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3})"
)


def parse_fail2ban(line: str) -> dict | None:
    m = _F2B_RE.match(line.rstrip("\n"))
    if not m:
        return None
    try:
        ts = int(datetime.strptime(m.group("t"), "%Y-%m-%d %H:%M:%S").timestamp())
    except ValueError:
        return None
    return {
        "ts_unix": ts,
        "jail": m.group("jail"),
        "action": m.group("action"),
        "ip": m.group("ip"),
        "raw": line.rstrip("\n"),
    }


def _insert_ssh(conn, rec: dict, host: str) -> None:
    conn.execute(
        """
        INSERT INTO ssh_events(ts_unix, event, username, src_ip, port, method,
                               host, raw, ingested_unix)
        VALUES (:ts_unix, :event, :username, :src_ip, :port, :method,
                :host, :raw, :ing)
        """,
        {**rec, "host": host, "ing": int(time.time())},
    )


def _insert_f2b(conn, rec: dict, host: str) -> None:
    conn.execute(
        """
        INSERT INTO fail2ban_events(ts_unix, jail, action, ip, host, raw, ingested_unix)
        VALUES (:ts_unix, :jail, :action, :ip, :host, :raw, :ing)
        """,
        {**rec, "host": host, "ing": int(time.time())},
    )


def process_available(conn, path, key, parse, insert, host, from_end: bool) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    size = p.stat().st_size
    saved = get_metadata(conn, key)
    offset = int(saved) if saved is not None else (size if from_end else 0)
    if offset > size:  # rotated/truncated — start over
        offset = 0
    count = 0
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        for line in fh:
            rec = parse(line)
            if rec:
                insert(conn, rec, host)
                count += 1
        new_offset = fh.tell()
    set_metadata(conn, key, str(new_offset))
    conn.commit()
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-log", default=DEFAULT_AUTH_LOG)
    ap.add_argument("--fail2ban-log", default=DEFAULT_FAIL2BAN_LOG)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--host", default="sweetpea", help="machine these logs came from")
    ap.add_argument("--poll", type=float, default=5.0, help="poll interval when following")
    ap.add_argument("--once", action="store_true", help="ingest available lines and exit")
    ap.add_argument("--from-end", action="store_true", help="skip existing lines on first run")
    args = ap.parse_args()

    conn = connect_db(args.db)  # provisions ssh_events / fail2ban_events via SCHEMA

    from_end = args.from_end
    while True:
        n = process_available(conn, args.auth_log, AUTH_OFFSET_KEY,
                              parse_auth, _insert_ssh, args.host, from_end)
        n += process_available(conn, args.fail2ban_log, F2B_OFFSET_KEY,
                              parse_fail2ban, _insert_f2b, args.host, from_end)
        from_end = False  # only the first pass honours --from-end
        if args.once:
            print(f"ingested {n} security event(s)")
            return 0
        time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
