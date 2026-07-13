import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
            """
            SELECT d.*, p.name AS person_name
            FROM devices d
            LEFT JOIN people p ON p.id = d.person_id
            ORDER BY d.last_seen_unix DESC
            """
        ).fetchall()
    return [_device_row(r) for r in rows]


def _resolve_mac(db, ident: str) -> str | None:
    """Map a device identifier to a MAC (the table's key).

    Devices are keyed on MAC, but callers that only know an IP (the DNS page —
    a DNS query carries a client_ip, never a MAC) pass one here. Try the MAC
    directly, then fall back to the device currently holding that IP, then the
    most recent lease that used it (covers a device that has since changed IP).
    """
    row = db.execute("SELECT mac FROM devices WHERE mac = ?", (ident,)).fetchone()
    if row:
        return row["mac"]
    row = db.execute(
        "SELECT mac FROM devices WHERE last_ip = ? ORDER BY last_seen_unix DESC LIMIT 1",
        (ident,),
    ).fetchone()
    if row:
        return row["mac"]
    row = db.execute(
        "SELECT mac FROM lease_observations WHERE ip = ? ORDER BY observed_unix DESC LIMIT 1",
        (ident,),
    ).fetchone()
    return row["mac"] if row else None


@router.get("/devices/{ident}")
def get_device(ident: str):
    with get_db() as db:
        mac = _resolve_mac(db, ident)
        if mac is None:
            raise HTTPException(status_code=404, detail="Device not found")
        row = db.execute(
            """
            SELECT d.*, p.name AS person_name
            FROM devices d
            LEFT JOIN people p ON p.id = d.person_id
            WHERE d.mac = ?
            """,
            (mac,),
        ).fetchone()
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


class LabelUpdate(BaseModel):
    label: str | None = None


@router.put("/devices/{ident}/label")
def set_label(ident: str, body: LabelUpdate):
    with get_db_rw() as db:
        mac = _resolve_mac(db, ident)
        if mac is None:
            raise HTTPException(status_code=404, detail="Device not found")
        db.execute(
            "UPDATE devices SET label = ?, updated_unix = ? WHERE mac = ?",
            (body.label or None, int(time.time()), mac),
        )
    return {"mac": mac, "label": body.label or None}


class PersonAssign(BaseModel):
    person_id: int | None = None


@router.put("/devices/{ident}/person")
def set_person(ident: str, body: PersonAssign):
    """Assign a device to a person (or unassign with person_id: null)."""
    with get_db_rw() as db:
        mac = _resolve_mac(db, ident)
        if mac is None:
            raise HTTPException(status_code=404, detail="Device not found")
        name = None
        if body.person_id is not None:
            row = db.execute(
                "SELECT name FROM people WHERE id = ?", (body.person_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Person not found")
            name = row["name"]
        db.execute(
            "UPDATE devices SET person_id = ?, updated_unix = ? WHERE mac = ?",
            (body.person_id, int(time.time()), mac),
        )
    return {"mac": mac, "person_id": body.person_id, "person_name": name}
