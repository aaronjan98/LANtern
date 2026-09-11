import glob
import os
import time

from fastapi import APIRouter, Query

from api.db import get_db

router = APIRouter()

# address=/domain/TARGET where TARGET is one of these = a blackhole (a block),
# as opposed to a local override that points at a real IP (e.g. .home -> .47).
_BLACKHOLE_TARGETS = {"0.0.0.0", "::", "#"}


def _read_blocklist(conf_dir: str) -> list[str]:
    """Domains blackholed via dnsmasq `address=/x/0.0.0.0` across the conf dir."""
    domains: list[str] = []
    for path in sorted(glob.glob(os.path.join(conf_dir, "*.conf"))):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("#") or not line.startswith("address=/"):
                        continue
                    parts = line[len("address=/"):].split("/")
                    if len(parts) >= 2 and parts[1] in _BLACKHOLE_TARGETS and parts[0]:
                        domains.append(parts[0])
        except OSError:
            continue
    seen: set[str] = set()
    return [d for d in domains if not (d in seen or seen.add(d))]


@router.get("/dns/blocked")
def dns_blocked(hours: int = Query(default=168, le=720)):
    """Domains blocked at the resolver, with how many times devices tried to reach
    them (and got blackholed) in the window, plus the device hitting them most."""
    cutoff = int(time.time()) - hours * 3600
    conf_dir = os.environ.get("DNSMASQ_CONF_DIR", "/data/dnsmasq.d")
    domains = _read_blocklist(conf_dir)
    out = []
    with get_db() as db:
        for dom in domains:
            like = f"%{dom}%"
            r = db.execute(
                "SELECT COUNT(*) hits, MAX(ts_unix) last, COUNT(DISTINCT client_ip) clients "
                "FROM dns_queries WHERE ts_unix>=? AND domain LIKE ?",
                (cutoff, like),
            ).fetchone()
            top = db.execute(
                "SELECT q.client_ip, d.label, d.hostname, COUNT(*) n "
                "FROM dns_queries q LEFT JOIN devices d ON d.last_ip=q.client_ip "
                "WHERE q.ts_unix>=? AND q.domain LIKE ? GROUP BY q.client_ip "
                "ORDER BY n DESC LIMIT 1",
                (cutoff, like),
            ).fetchone()
            top_device = None
            if top and top["n"]:
                top_device = top["label"] or top["hostname"] or top["client_ip"]
            out.append({
                "domain": dom,
                "hits": r["hits"] or 0,
                "clients": r["clients"] or 0,
                "last_unix": r["last"],
                "top_device": top_device,
            })
    out.sort(key=lambda x: x["hits"], reverse=True)
    return {"count": len(domains), "hours": hours, "blocked": out}


@router.get("/dns/recent")
def dns_recent(
    limit: int = Query(default=50, le=500),
    client_ip: str | None = None,
):
    with get_db() as db:
        if client_ip:
            rows = db.execute(
                """
                SELECT ts_unix, ts, client_ip, qtype, domain
                FROM dns_queries
                WHERE client_ip = ?
                ORDER BY ts_unix DESC, id DESC
                LIMIT ?
                """,
                (client_ip, limit),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT ts_unix, ts, client_ip, qtype, domain
                FROM dns_queries
                ORDER BY ts_unix DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


@router.get("/dns/top")
def dns_top(
    minutes: int = Query(default=60, le=10080),
    limit: int = Query(default=20, le=100),
):
    cutoff = int(time.time()) - (minutes * 60)
    with get_db() as db:
        rows = db.execute(
            """
            SELECT domain, COUNT(*) AS queries, COUNT(DISTINCT client_ip) AS clients
            FROM dns_queries
            WHERE ts_unix >= ?
            GROUP BY domain
            ORDER BY queries DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/dns/clients")
def dns_clients(minutes: int = Query(default=60, le=10080)):
    cutoff = int(time.time()) - (minutes * 60)
    with get_db() as db:
        rows = db.execute(
            """
            SELECT
                d.hostname,
                q.client_ip,
                COUNT(*) AS queries
            FROM dns_queries q
            LEFT JOIN devices d ON d.last_ip = q.client_ip
            WHERE q.ts_unix >= ?
            GROUP BY q.client_ip
            ORDER BY queries DESC
            """,
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/dns/history")
def dns_history(
    bucket_minutes: int = Query(default=60, ge=1, le=1440),
    hours: int = Query(default=24, le=168),
    client_ip: str | None = None,
):
    """Query volume bucketed over time — for the sparkline chart."""
    cutoff = int(time.time()) - (hours * 3600)
    bucket_secs = bucket_minutes * 60
    with get_db() as db:
        if client_ip:
            rows = db.execute(
                """
                SELECT
                    (ts_unix / ?) * ? AS bucket,
                    COUNT(*) AS queries
                FROM dns_queries
                WHERE ts_unix >= ? AND client_ip = ?
                GROUP BY bucket
                ORDER BY bucket ASC
                """,
                (bucket_secs, bucket_secs, cutoff, client_ip),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT
                    (ts_unix / ?) * ? AS bucket,
                    COUNT(*) AS queries
                FROM dns_queries
                WHERE ts_unix >= ?
                GROUP BY bucket
                ORDER BY bucket ASC
                """,
                (bucket_secs, bucket_secs, cutoff),
            ).fetchall()
    return [dict(r) for r in rows]
