"""Per-domain drill-down over http_requests -- "who is hitting this vhost, and
with what." The DNS tab's "top domains" answers a different question (who
*looked up* this name); this answers who actually connected and what they
did, for any vhost behind sweetpea's one nginx (local, Tailscale, and
Cloudflare access alike -- see lantern.nginx_ingest).

Requires the `host` column (added 2026-10-08, via nginx's $host appended to
log_format main) -- rows ingested before that are NULL and simply won't
match any domain filter here, same as any other schema addition.
"""

import time

from fastapi import APIRouter, Query

from api.db import get_db

router = APIRouter()


def _path_base_expr(col: str = "path") -> str:
    """SQL expression stripping a path's query string for aggregation --
    otherwise share-link tokens, image tags, etc. make every row distinct."""
    return f"CASE WHEN instr({col}, '?') > 0 THEN substr({col}, 1, instr({col}, '?') - 1) ELSE {col} END"


@router.get("/domains/{domain}/summary")
def domain_summary(domain: str, hours: int = Query(default=168, ge=1, le=8760)):
    cutoff = int(time.time()) - hours * 3600
    with get_db() as db:
        totals = db.execute(
            "SELECT COUNT(*) requests, COUNT(DISTINCT client_ip) clients, "
            "MIN(ts_unix) first_seen, MAX(ts_unix) last_seen "
            "FROM http_requests WHERE host = ? AND ts_unix >= ?",
            (domain, cutoff),
        ).fetchone()
        statuses = db.execute(
            "SELECT status, COUNT(*) n FROM http_requests "
            "WHERE host = ? AND ts_unix >= ? GROUP BY status ORDER BY n DESC",
            (domain, cutoff),
        ).fetchall()
    return {
        "domain": domain,
        "hours": hours,
        "requests": totals["requests"],
        "clients": totals["clients"],
        "first_seen": totals["first_seen"],
        "last_seen": totals["last_seen"],
        "statuses": [dict(r) for r in statuses],
    }


@router.get("/domains/{domain}/clients")
def domain_clients(
    domain: str,
    hours: int = Query(default=168, ge=1, le=8760),
    limit: int = Query(default=30, le=200),
):
    """Top client IPs hitting this domain -- labeled to a device/person where
    LANtern has one, same join the DNS tab already uses. An unlabeled, non-LAN
    IP here is someone (or something) from outside your network."""
    cutoff = int(time.time()) - hours * 3600
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
                h.client_ip,
                d.label, d.hostname, d.vendor,
                COUNT(*) AS requests,
                COUNT(DISTINCT h.user_agent) AS distinct_user_agents,
                MAX(h.ts_unix) AS last_seen
            FROM http_requests h
            LEFT JOIN devices d ON d.last_ip = h.client_ip
            WHERE h.host = ? AND h.ts_unix >= ?
            GROUP BY h.client_ip
            ORDER BY requests DESC
            LIMIT ?
            """,
            (domain, cutoff, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_lan"] = d["client_ip"].startswith("10.0.50.") if d["client_ip"] else False
        out.append(d)
    return out


@router.get("/domains/{domain}/paths")
def domain_paths(
    domain: str,
    hours: int = Query(default=168, ge=1, le=8760),
    limit: int = Query(default=30, le=200),
):
    """Top paths (query string stripped) -- which endpoints/sections, not
    individual share-link tokens or image URLs."""
    cutoff = int(time.time()) - hours * 3600
    path_base = _path_base_expr("path")
    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT
                {path_base} AS path_base,
                COUNT(*) AS requests,
                COUNT(DISTINCT client_ip) AS clients,
                MAX(ts_unix) AS last_seen
            FROM http_requests
            WHERE host = ? AND ts_unix >= ?
            GROUP BY path_base
            ORDER BY requests DESC
            LIMIT ?
            """,
            (domain, cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/domains/{domain}/recent")
def domain_recent(
    domain: str,
    hours: int = Query(default=168, ge=1, le=8760),
    client_ip: str | None = None,
    limit: int = Query(default=100, le=1000),
):
    """Raw recent requests -- the detail table under the summary/breakdowns."""
    cutoff = int(time.time()) - hours * 3600
    where = "WHERE host = ? AND ts_unix >= ?"
    params: list = [domain, cutoff]
    if client_ip:
        where += " AND client_ip = ?"
        params.append(client_ip)
    with get_db() as db:
        rows = db.execute(
            f"SELECT ts_unix, client_ip, method, path, status, user_agent "
            f"FROM http_requests {where} ORDER BY ts_unix DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]
