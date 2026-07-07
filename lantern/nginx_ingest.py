#!/usr/bin/env python3
"""Tail the nginx reverse-proxy access log into LANtern (http_requests).

This is LANtern's *service-usage* vantage point. Every hosted service sits
behind one nginx on sweetpea, so its access log captures local, Tailscale, and
Cloudflare access alike — traffic the router-based NetFlow collector cannot see,
because local client<->server flows are east-west switched and never reach the
router. Feeds is_homelab_idle in lantern.activity.

Mirrors lantern.dnsmasq_ingest: it tails a file, remembering its byte offset in
the metadata table so it resumes across restarts without re-ingesting.
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

from lantern.common import DEFAULT_DB, connect_db, get_metadata, set_metadata

DEFAULT_LOG = "/data/nginx-logs"  # a directory (tails every *.log) or a single file
OFFSET_KEY = "nginx.offset"

# The http_requests table now lives in lantern.common.SCHEMA (connect_db
# provisions it), alongside every other LANtern table.

# nginx log_format main:
#   $remote_addr - $remote_user [$time_local] "$request" $status
#   $body_bytes_sent "$http_referer" "$http_user_agent" "$http_x_forwarded_for"
LINE_RE = re.compile(
    r'^(?P<ip>\S+) - \S+ \[(?P<t>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+)[^"]*" '
    r'(?P<status>\d{3}) (?P<bytes>\d+) '
    r'"(?P<ref>[^"]*)" "(?P<ua>[^"]*)"'
    r'(?: "(?P<xff>[^"]*)")?'  # main format has XFF; per-vhost "combined" does not
)


def parse_line(line: str) -> dict | None:
    m = LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    try:
        ts = int(datetime.strptime(m.group("t"), "%d/%b/%Y:%H:%M:%S %z").timestamp())
    except ValueError:
        return None
    return {
        "ts_unix": ts,
        "client_ip": m.group("ip"),
        "method": m.group("method"),
        "path": m.group("path"),
        "status": int(m.group("status")),
        "bytes": int(m.group("bytes")),
        "referer": m.group("ref") or None,
        "user_agent": m.group("ua") or None,
        "x_forwarded_for": (m.group("xff") or None) if m.group("xff") not in (None, "-") else None,
        "raw": line.rstrip("\n"),
    }


def _insert(conn, rec: dict) -> None:
    conn.execute(
        """
        INSERT INTO http_requests(ts_unix, client_ip, method, path, status, bytes,
                                  user_agent, referer, x_forwarded_for, raw, ingested_unix)
        VALUES (:ts_unix, :client_ip, :method, :path, :status, :bytes,
                :user_agent, :referer, :x_forwarded_for, :raw, :ingested)
        """,
        {**rec, "ingested": int(time.time())},
    )


def _log_files(path: str) -> list[Path]:
    """Resolve a path to the log file(s) to tail.

    A directory → every access log in it (all *.log except error logs, which
    aren't request records). A file → just that file.
    """
    p = Path(path)
    if p.is_dir():
        return sorted(f for f in p.glob("*.log") if not f.name.startswith("error"))
    return [p]


def process_available(conn, path, state_key: str, from_end: bool) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    size = p.stat().st_size
    saved = get_metadata(conn, state_key)
    offset = int(saved) if saved is not None else (size if from_end else 0)
    if offset > size:  # log was rotated/truncated — start over
        offset = 0
    count = 0
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        for line in fh:
            rec = parse_line(line)
            if rec:
                _insert(conn, rec)
                count += 1
        new_offset = fh.tell()
    set_metadata(conn, state_key, str(new_offset))
    conn.commit()
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", default=DEFAULT_LOG, help=f"nginx access log, default {DEFAULT_LOG}")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path, default {DEFAULT_DB}")
    ap.add_argument("--poll", type=float, default=2.0, help="poll interval when following")
    ap.add_argument("--once", action="store_true", help="ingest available lines and exit")
    ap.add_argument("--from-end", action="store_true", help="skip existing lines on first run")
    ap.add_argument("--state-key", default=OFFSET_KEY)
    args = ap.parse_args()

    conn = connect_db(args.db)  # provisions http_requests via common.SCHEMA

    from_end = args.from_end
    while True:
        n = 0
        for f in _log_files(args.log):
            # one offset per file so per-vhost logs resume independently
            n += process_available(conn, f, f"{args.state_key}:{f.name}", from_end)
        from_end = False  # only the first pass honours --from-end
        if args.once:
            print(f"ingested {n} nginx request(s)")
            return 0
        time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
