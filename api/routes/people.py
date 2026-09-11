import sqlite3
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.db import get_db, get_db_rw

router = APIRouter()

# Seconds of REAL DNS/netflow traffic that count as "on the LAN". A live DHCP
# lease is NOT presence -- that's the bug this replaces (a device that left days
# ago keeps a valid 7-day lease, so last_seen looked "now").
LAN_WINDOW = 900


class PersonBody(BaseModel):
    name: str
    note: str | None = None


def _ip_active(db, ip, cutoff) -> bool:
    """Did this IP actually do anything (DNS query or any flow) within the window?"""
    if not ip:
        return False
    if db.execute(
        "SELECT 1 FROM dns_queries WHERE client_ip=? AND ts_unix>=? LIMIT 1",
        (ip, cutoff),
    ).fetchone():
        return True
    for col in ("src_addr", "dst_addr"):
        if db.execute(
            f"SELECT 1 FROM netflow_records WHERE {col}=? AND received_unix>=? LIMIT 1",
            (ip, cutoff),
        ).fetchone():
            return True
    return False


def _tailscale_peer(db, ts_ip):
    if not ts_ip:
        return None
    try:
        return db.execute(
            "SELECT online, dns_name FROM tailscale_peers WHERE ts_ip=?", (ts_ip,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def _presence(db, person, now):
    """(status, on_lan, on_tailscale, ts_name). status = home|remote|offline.

    home = real LAN traffic from one of their devices; remote = their tailnet
    device is online but not on the LAN; offline = neither. Fully passive.
    """
    cutoff = now - LAN_WINDOW
    # (a) explicitly-assigned devices (by MAC)
    on_lan = any(
        _ip_active(db, d["last_ip"], cutoff)
        for d in db.execute(
            "SELECT last_ip FROM devices WHERE person_id=?", (person["id"],)
        ).fetchall()
    )
    # (b) any device the phone currently reports under this person's stable DHCP
    #     name -- handles MAC randomization (a new randomized MAC is a new device
    #     record, but the phone keeps advertising the same name).
    lan_hostname = person["lan_hostname"] if "lan_hostname" in person.keys() else None
    if not on_lan and lan_hostname:
        on_lan = any(
            _ip_active(db, d["last_ip"], cutoff)
            for d in db.execute(
                "SELECT last_ip FROM devices WHERE hostname=? AND last_ip LIKE '10.0.50.%'",
                (lan_hostname,),
            ).fetchall()
        )
    ts_ip = person["tailscale_ip"] if "tailscale_ip" in person.keys() else None
    tp = _tailscale_peer(db, ts_ip)
    on_tailscale = bool(tp["online"]) if tp else False
    ts_name = tp["dns_name"] if tp else None
    status = "home" if on_lan else ("remote" if on_tailscale else "offline")
    return status, on_lan, on_tailscale, ts_name


@router.get("/people")
def list_people():
    """People with real presence — home (LAN) / remote (Tailscale) / offline.
    Guests are hidden unless currently present."""
    now = int(time.time())
    with get_db() as db:
        people = db.execute(
            "SELECT id, name, note, tailscale_ip, is_guest, lan_hostname FROM people "
            "ORDER BY name COLLATE NOCASE"
        ).fetchall()
        out = []
        for p in people:
            device_count = db.execute(
                "SELECT COUNT(*) c FROM devices WHERE person_id=?", (p["id"],)
            ).fetchone()["c"]
            status, on_lan, on_tailscale, ts_name = _presence(db, p, now)
            present = on_lan or on_tailscale
            if p["is_guest"] and not present:
                continue
            out.append({
                "id": p["id"], "name": p["name"], "note": p["note"],
                "device_count": device_count,
                "status": status, "on_lan": on_lan, "on_tailscale": on_tailscale,
                "online": present, "is_guest": bool(p["is_guest"]),
                "tailscale_name": ts_name,
            })
    return out


@router.get("/people/online")
def people_online():
    """Who — as a person — is present right now (home or remote), for the voice
    assistant's 'who's online' question."""
    now = int(time.time())
    with get_db() as db:
        people = db.execute(
            "SELECT id, name, tailscale_ip, is_guest, lan_hostname FROM people "
            "ORDER BY name COLLATE NOCASE"
        ).fetchall()
        out = []
        for p in people:
            status, on_lan, on_tailscale, _ = _presence(db, p, now)
            if on_lan or on_tailscale:
                out.append({"id": p["id"], "name": p["name"], "status": status})
    return out


@router.get("/people/{person_id}")
def get_person(person_id: int):
    now = int(time.time())
    cutoff = now - LAN_WINDOW
    with get_db() as db:
        p = db.execute(
            "SELECT id, name, note, tailscale_ip, is_guest, lan_hostname FROM people WHERE id = ?",
            (person_id,),
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
        status, on_lan, on_tailscale, ts_name = _presence(db, p, now)
        devices = []
        for r in devs:
            dd = dict(r)
            dd["active"] = _ip_active(db, dd["last_ip"], cutoff)  # real traffic, not lease
            devices.append(dd)
    person = {
        "id": p["id"], "name": p["name"], "note": p["note"],
        "status": status, "on_lan": on_lan, "on_tailscale": on_tailscale,
        "tailscale_name": ts_name, "is_guest": bool(p["is_guest"]),
    }
    return {"person": person, "devices": devices}


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
