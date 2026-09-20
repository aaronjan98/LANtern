"""Deco reboot endpoints — history + the human controls (request / approve / cancel).

Reads are on the API's read-only connection; the three POSTs use get_db_rw. The
brain (lantern.deco_reboot) and the host runner own the rest of the state machine;
these endpoints only ever create or cancel a request, or approve a pending one.

Identity: when lantern is fronted by oauth2-proxy, the authenticated user arrives
as an ``X-Forwarded-*`` header, which we record as who requested/approved. On the
LAN/Tailscale-only path (no proxy) that header is absent, so the actor is
``lan-user`` -- the network boundary is the auth boundary there.
"""

import json
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from api.db import get_db, get_db_rw

router = APIRouter()

OPEN_STATUSES = ("pending", "approved", "ready", "executing")
_OPEN_PLACEHOLDERS = ",".join("?" * len(OPEN_STATUSES))


class RequestBody(BaseModel):
    reason: str | None = None
    scheduled_unix: int | None = None


class ApproveBody(BaseModel):
    scheduled_unix: int | None = None


def _actor(request: Request) -> str:
    for header in (
        "x-forwarded-preferred-username",
        "x-forwarded-user",
        "x-forwarded-email",
    ):
        value = request.headers.get(header)
        if value:
            return value
    return "lan-user"


def _serialize(row) -> dict:
    d = dict(row)
    evidence = d.pop("idle_evidence_json", None)
    d["idle_evidence"] = json.loads(evidence) if evidence else None
    return d


@router.get("/reboot/events")
def reboot_events(limit: int = Query(50, ge=1, le=500)):
    """Reboot history + any in-flight request, newest first."""
    with get_db() as db:
        try:
            rows = db.execute(
                "SELECT * FROM deco_reboot_events ORDER BY created_unix DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # table not provisioned yet
    return [_serialize(r) for r in rows]


@router.get("/reboot/status")
def reboot_status():
    """Summary for the UI header: last successful reboot + any open request."""
    with get_db() as db:
        try:
            last = db.execute(
                "SELECT MAX(finished_unix) AS ts FROM deco_reboot_events "
                "WHERE status='succeeded'"
            ).fetchone()["ts"]
            open_row = db.execute(
                f"SELECT * FROM deco_reboot_events WHERE status IN "
                f"({_OPEN_PLACEHOLDERS}) ORDER BY created_unix LIMIT 1",
                OPEN_STATUSES,
            ).fetchone()
        except sqlite3.OperationalError:
            last, open_row = None, None
    return {
        "last_success_unix": last,
        "open_request": _serialize(open_row) if open_row else None,
    }


@router.post("/reboot/request")
def reboot_request(request: Request, body: RequestBody | None = None):
    """Human 'Reboot now' (optionally 'Schedule for <time>' -- same endpoint, an
    optional scheduled_unix): create an auto-approved request. It still waits for
    an idle window at or after that time (immediately, if none given) before the
    host runner executes it."""
    actor = _actor(request)
    now = int(time.time())
    scheduled = body.scheduled_unix if body else None
    if scheduled is not None and scheduled <= now:
        raise HTTPException(400, "Scheduled time must be in the future")
    try:
        with get_db_rw() as db:
            if db.execute(
                f"SELECT id FROM deco_reboot_events WHERE status IN "
                f"({_OPEN_PLACEHOLDERS}) LIMIT 1",
                OPEN_STATUSES,
            ).fetchone():
                raise HTTPException(409, "A reboot request is already in flight")
            cur = db.execute(
                "INSERT INTO deco_reboot_events "
                "(created_unix, trigger, status, requested_by, approved_by, "
                " approved_unix, scheduled_unix, detail, updated_unix) "
                "VALUES (?, 'manual', 'approved', ?, ?, ?, ?, ?, ?)",
                (now, actor, actor, now, scheduled, body.reason if body else None, now),
            )
            rid = cur.lastrowid
    except sqlite3.OperationalError:
        raise HTTPException(503, "Reboot subsystem still initializing")
    return {"id": rid, "status": "approved", "requested_by": actor, "scheduled_unix": scheduled}


@router.post("/reboot/{event_id}/approve")
def reboot_approve(event_id: int, request: Request, body: ApproveBody | None = None):
    """Approve a pending request. body.scheduled_unix optionally overrides the
    auto-suggested overnight slot with a custom future time before confirming."""
    actor = _actor(request)
    now = int(time.time())
    with get_db_rw() as db:
        row = db.execute(
            "SELECT status, scheduled_unix FROM deco_reboot_events WHERE id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "No such reboot request")
        if row["status"] != "pending":
            raise HTTPException(409, f"Request is {row['status']}, not pending")
        scheduled = body.scheduled_unix if body and body.scheduled_unix is not None else row["scheduled_unix"]
        if scheduled is not None and scheduled <= now:
            raise HTTPException(400, "Scheduled time must be in the future")
        db.execute(
            "UPDATE deco_reboot_events SET status='approved', approved_by=?, "
            "approved_unix=?, scheduled_unix=?, updated_unix=? WHERE id=? AND status='pending'",
            (actor, now, scheduled, now, event_id),
        )
    return {"id": event_id, "status": "approved", "approved_by": actor, "scheduled_unix": scheduled}


@router.post("/reboot/{event_id}/cancel")
def reboot_cancel(event_id: int, request: Request):
    """Cancel a request that hasn't started power-cycling yet."""
    now = int(time.time())
    with get_db_rw() as db:
        row = db.execute(
            "SELECT status FROM deco_reboot_events WHERE id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "No such reboot request")
        if row["status"] not in ("pending", "approved", "ready"):
            raise HTTPException(409, f"Cannot cancel a {row['status']} request")
        db.execute(
            "UPDATE deco_reboot_events SET status='canceled', "
            "detail=COALESCE(detail || ' ', '') || ?, updated_unix=? WHERE id=?",
            (f"canceled by {_actor(request)}", now, event_id),
        )
    return {"id": event_id, "status": "canceled"}
