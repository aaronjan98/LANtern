"""Jellyfin watch-history ingest + read endpoints.

Jellyfin runs on qwerty, a different host than every other LANtern container
(all colocated on sweetpea), so this deliberately does NOT follow the usual
*_ingest module shape (a colocated container tailing a local file and writing
straight into lantern.db). A small script on qwerty
(qwerty-scripts/jellyfin-lantern-report) reads the Playback Reporting plugin's
own SQLite db locally -- avoiding the correctness problems of querying SQLite
over the network against a live writer -- and pushes new rows here on a timer.
Same "the reader lives on the host that owns the resource" principle as the
Deco reboot's host runner, just pushing instead of executing.

The ingest route sits behind a shared-secret header, not oauth2-proxy human
login (qwerty has no browser to sign in with): sweetpea's lantern.home vhost
carves out /api/ingest/ to bypass the oauth2-proxy hop for exactly this path.
"""

import os
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from api.db import get_db, get_db_rw

router = APIRouter()

INGEST_TOKEN_ENV = "LANTERN_INGEST_TOKEN"


class PlaybackEvent(BaseModel):
    ts_unix: int
    user_id: str
    username: str | None = None
    item_id: str | None = None
    item_type: str | None = None
    item_name: str | None = None
    playback_method: str | None = None
    client_name: str | None = None
    device_name: str | None = None
    play_duration: int | None = None


class IngestBody(BaseModel):
    host: str
    events: list[PlaybackEvent]


def _check_ingest_auth(request: Request) -> None:
    expected = os.environ.get(INGEST_TOKEN_ENV)
    if not expected or request.headers.get("x-lantern-ingest-key") != expected:
        raise HTTPException(401, "Missing or invalid ingest key")


@router.post("/ingest/jellyfin_playback")
def ingest_jellyfin_playback(body: IngestBody, request: Request):
    """Idempotent by design, not by cursor: the push script resends a trailing
    window every run and relies on the UNIQUE(host, ts_unix, user_id, item_id)
    constraint here to make the overlap a no-op. A missed run or a restart on
    either side just gets naturally re-covered on the next push."""
    _check_ingest_auth(request)
    now = int(time.time())
    inserted = 0
    try:
        with get_db_rw() as db:
            for e in body.events:
                cur = db.execute(
                    """
                    INSERT OR IGNORE INTO jellyfin_playback
                        (ts_unix, user_id, username, item_id, item_type, item_name,
                         playback_method, client_name, device_name, play_duration,
                         host, ingested_unix)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (e.ts_unix, e.user_id, e.username, e.item_id, e.item_type,
                     e.item_name, e.playback_method, e.client_name, e.device_name,
                     e.play_duration, body.host, now),
                )
                inserted += cur.rowcount
    except sqlite3.OperationalError:
        raise HTTPException(503, "Jellyfin ingest subsystem still initializing")
    return {"received": len(body.events), "inserted": inserted}


@router.get("/jellyfin/playback")
def jellyfin_playback(
    hours: int = Query(default=168, ge=1, le=87600),  # up to 10y -- AJ keeps this data forever
    username: str | None = None,
    limit: int = Query(default=200, le=20000),
):
    """Recent plays, newest first -- the raw activity table."""
    cutoff = int(time.time()) - hours * 3600
    where = "WHERE ts_unix >= ?"
    params: list = [cutoff]
    if username:
        where += " AND username = ?"
        params.append(username)
    with get_db() as db:
        try:
            rows = db.execute(
                f"SELECT * FROM jellyfin_playback {where} "
                f"ORDER BY ts_unix DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


@router.get("/jellyfin/summary")
def jellyfin_summary(hours: int = Query(default=168, ge=1, le=87600)):
    """Headline totals + per-user and per-title breakdowns over the window."""
    cutoff = int(time.time()) - hours * 3600
    with get_db() as db:
        try:
            totals = db.execute(
                "SELECT COUNT(*) plays, COALESCE(SUM(play_duration),0) seconds, "
                "COUNT(DISTINCT username) active_users "
                "FROM jellyfin_playback WHERE ts_unix >= ?",
                (cutoff,),
            ).fetchone()
            by_user = db.execute(
                "SELECT username, COUNT(*) plays, COALESCE(SUM(play_duration),0) seconds, "
                "MAX(ts_unix) last_seen "
                "FROM jellyfin_playback WHERE ts_unix >= ? "
                "GROUP BY username ORDER BY seconds DESC",
                (cutoff,),
            ).fetchall()
            top_titles = db.execute(
                "SELECT item_name, item_type, COUNT(*) plays, "
                "COALESCE(SUM(play_duration),0) seconds "
                "FROM jellyfin_playback WHERE ts_unix >= ? "
                "GROUP BY item_name ORDER BY seconds DESC LIMIT 10",
                (cutoff,),
            ).fetchall()
        except sqlite3.OperationalError:
            return {"hours": hours, "plays": 0, "seconds": 0, "active_users": 0,
                     "by_user": [], "top_titles": []}
    return {
        "hours": hours,
        "plays": totals["plays"],
        "seconds": totals["seconds"],
        "active_users": totals["active_users"],
        "by_user": [dict(r) for r in by_user],
        "top_titles": [dict(r) for r in top_titles],
    }
