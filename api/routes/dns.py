import time
from fastapi import APIRouter, Query
from api.db import get_db

router = APIRouter()


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
