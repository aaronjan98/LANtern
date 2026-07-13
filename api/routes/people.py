import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.db import get_db, get_db_rw

router = APIRouter()

ACTIVE_THRESHOLD = 300  # seconds — matches devices.py


class PersonBody(BaseModel):
    name: str
    note: str | None = None


@router.get("/people")
def list_people():
    """People with device counts and current online status (any device active)."""
    cutoff = int(time.time()) - ACTIVE_THRESHOLD
    with get_db() as db:
        rows = db.execute(
            """
            SELECT p.id, p.name, p.note,
                   COUNT(d.mac) AS device_count,
                   SUM(CASE WHEN d.last_seen_unix >= ? THEN 1 ELSE 0 END) AS active_devices
            FROM people p
            LEFT JOIN devices d ON d.person_id = p.id
            GROUP BY p.id
            ORDER BY p.name COLLATE NOCASE
            """,
            (cutoff,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["active_devices"] = d["active_devices"] or 0
        d["online"] = d["active_devices"] > 0
        out.append(d)
    return out


@router.get("/people/online")
def people_online():
    """Who — as a person — is online right now, with their active devices.

    This is the endpoint the voice assistant asks for "who's home / online".
    """
    cutoff = int(time.time()) - ACTIVE_THRESHOLD
    with get_db() as db:
        rows = db.execute(
            """
            SELECT p.id, p.name, d.mac, d.hostname, d.label, d.last_ip
            FROM people p
            JOIN devices d ON d.person_id = p.id
            WHERE d.last_seen_unix >= ?
            ORDER BY p.name COLLATE NOCASE
            """,
            (cutoff,),
        ).fetchall()
    people: dict[int, dict] = {}
    for r in rows:
        person = people.setdefault(r["id"], {"id": r["id"], "name": r["name"], "devices": []})
        person["devices"].append(r["label"] or r["hostname"] or r["last_ip"] or r["mac"])
    return list(people.values())


@router.get("/people/{person_id}")
def get_person(person_id: int):
    cutoff = int(time.time()) - ACTIVE_THRESHOLD
    with get_db() as db:
        p = db.execute(
            "SELECT id, name, note FROM people WHERE id = ?", (person_id,)
        ).fetchone()
        if p is None:
            raise HTTPException(status_code=404, detail="Person not found")
        devs = db.execute(
            """
            SELECT mac, hostname, label, last_ip, vendor, last_seen_unix
            FROM devices WHERE person_id = ?
            ORDER BY last_seen_unix DESC
            """,
            (person_id,),
        ).fetchall()
    devices = []
    for r in devs:
        dd = dict(r)
        dd["active"] = dd["last_seen_unix"] is not None and dd["last_seen_unix"] >= cutoff
        devices.append(dd)
    return {"person": dict(p), "devices": devices}


@router.post("/people")
def create_person(body: PersonBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    now = int(time.time())
    with get_db_rw() as db:
        cur = db.execute(
            "INSERT INTO people(name, note, created_unix, updated_unix) VALUES (?, ?, ?, ?)",
            (name, body.note or None, now, now),
        )
        pid = cur.lastrowid
    return {"id": pid, "name": name, "note": body.note or None}


@router.put("/people/{person_id}")
def update_person(person_id: int, body: PersonBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    with get_db_rw() as db:
        if db.execute("SELECT id FROM people WHERE id = ?", (person_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Person not found")
        db.execute(
            "UPDATE people SET name = ?, note = ?, updated_unix = ? WHERE id = ?",
            (name, body.note or None, int(time.time()), person_id),
        )
    return {"id": person_id, "name": name, "note": body.note or None}


@router.delete("/people/{person_id}")
def delete_person(person_id: int):
    """Delete a person; their devices are unassigned, not deleted."""
    with get_db_rw() as db:
        db.execute("UPDATE devices SET person_id = NULL WHERE person_id = ?", (person_id,))
        db.execute("DELETE FROM people WHERE id = ?", (person_id,))
    return {"deleted": person_id}
