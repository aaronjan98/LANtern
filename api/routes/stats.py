import time
from fastapi import APIRouter
from api.db import get_db

router = APIRouter()


@router.get("/health")
def health():
    """Cheap end-to-end check: process up AND the db is actually reachable
    through it -- not just a TCP accept. Point uptime monitoring at THIS, not
    bare '/' on the ui container: the static app shell serves 200 even when
    every /api/ call is dead (2026-09-29 -- ui's nginx held a stale upstream
    IP for the api container after a redeploy; Kuma's root-path check stayed
    green the entire ~25min outage because it never touches /api/ at all)."""
    with get_db() as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok"}


@router.get("/stats")
def get_stats():
    with get_db() as db:
        tables = [
            "devices", "lease_observations", "dns_queries",
            "dhcp_events", "netflow_records", "netflow_templates",
        ]
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

        five_min_ago = int(time.time()) - 300
        counts["active_devices"] = db.execute(
            "SELECT COUNT(*) FROM devices WHERE last_seen_unix >= ?", (five_min_ago,)
        ).fetchone()[0]

        today_start = int(time.time()) - 86400
        counts["dns_queries_today"] = db.execute(
            "SELECT COUNT(*) FROM dns_queries WHERE ts_unix >= ?", (today_start,)
        ).fetchone()[0]

        top = db.execute(
            """
            SELECT domain, COUNT(*) AS cnt FROM dns_queries
            WHERE ts_unix >= ?
            GROUP BY domain ORDER BY cnt DESC LIMIT 1
            """,
            (int(time.time()) - 3600,),
        ).fetchone()
        counts["top_domain_last_hour"] = dict(top) if top else None

        bytes_row = db.execute(
            "SELECT SUM(bytes) FROM netflow_records WHERE received_unix >= ?", (today_start,)
        ).fetchone()
        counts["bytes_today"] = bytes_row[0] or 0

    return counts
