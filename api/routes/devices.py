import time
from fastapi import APIRouter, HTTPException
from api.db import get_db, get_db_rw

router = APIRouter()

ACTIVE_THRESHOLD = 300  # seconds


def _device_row(row) -> dict:
    d = dict(row)
    d["active"] = (
        d["last_seen_unix"] is not None
        and d["last_seen_unix"] >= int(time.time()) - ACTIVE_THRESHOLD
    )
    return d


@router.get("/devices")
def list_devices():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM devices ORDER BY last_seen_unix DESC"
        ).fetchall()
    return [_device_row(r) for r in rows]


@router.get("/devices/{mac}")
def get_device(mac: str):
    with get_db() as db:
        row = db.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Device not found")

        ip_history = db.execute(
            """
            SELECT ip, hostname, observed_unix, lease_expires_unix
            FROM lease_observations
            WHERE mac = ?
            ORDER BY observed_unix DESC
            LIMIT 100
            """,
            (mac,),
        ).fetchall()

    return {
        "device": _device_row(row),
        "ip_history": [dict(r) for r in ip_history],
    }

from pydantic import BaseModel


class LabelUpdate(BaseModel):
    label: str | None = None


@router.put("/devices/{mac}/label")
def set_label(mac: str, body: LabelUpdate):
    with get_db_rw() as db:
        row = db.execute("SELECT mac FROM devices WHERE mac = ?", (mac,)).fetchone()
        if row is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Device not found")
        db.execute(
            "UPDATE devices SET label = ?, updated_unix = ? WHERE mac = ?",
            (body.label or None, int(__import__("time").time()), mac),
        )
    return {"mac": mac, "label": body.label or None}
