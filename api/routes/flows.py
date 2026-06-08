import time
from fastapi import APIRouter, Query
from api.db import get_db

router = APIRouter()

PROTO_NAMES = {1: "ICMP", 6: "TCP", 17: "UDP", 58: "ICMPv6"}


def _format_flow(row: dict) -> dict:
    row["protocol_name"] = PROTO_NAMES.get(row["protocol"], str(row["protocol"]) if row["protocol"] else "-")
    return row


@router.get("/flows/recent")
def flows_recent(
    limit: int = Query(default=50, le=500),
    host: str | None = None,
):
    with get_db() as db:
        if host:
            rows = db.execute(
                """
                SELECT received_unix, src_addr, src_port, dst_addr, dst_port,
                       protocol, packets, bytes, first_unix, last_unix
                FROM netflow_records
                WHERE src_addr = ? OR dst_addr = ?
                ORDER BY received_unix DESC, id DESC
                LIMIT ?
                """,
                (host, host, limit),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT received_unix, src_addr, src_port, dst_addr, dst_port,
                       protocol, packets, bytes, first_unix, last_unix
                FROM netflow_records
                ORDER BY received_unix DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [_format_flow(dict(r)) for r in rows]


@router.get("/flows/top-talkers")
def flows_top_talkers(minutes: int = Query(default=60, le=1440)):
    cutoff = int(time.time()) - (minutes * 60)
    with get_db() as db:
        top_src = db.execute(
            """
            SELECT src_addr AS addr, SUM(bytes) AS bytes, COUNT(*) AS flows
            FROM netflow_records
            WHERE received_unix >= ? AND src_addr IS NOT NULL
            GROUP BY src_addr
            ORDER BY bytes DESC
            LIMIT 10
            """,
            (cutoff,),
        ).fetchall()

        top_dst = db.execute(
            """
            SELECT dst_addr AS addr, SUM(bytes) AS bytes, COUNT(*) AS flows
            FROM netflow_records
            WHERE received_unix >= ? AND dst_addr IS NOT NULL
            GROUP BY dst_addr
            ORDER BY bytes DESC
            LIMIT 10
            """,
            (cutoff,),
        ).fetchall()

    return {
        "top_sources": [dict(r) for r in top_src],
        "top_destinations": [dict(r) for r in top_dst],
    }
