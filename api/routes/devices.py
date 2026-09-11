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


@router.get("/devices/new")
def new_devices():
    """Review inbox: unidentified devices (no label, no person, not dismissed) seen
    recently, each with a DNS-fingerprint auto-guess to speed up labeling.

    Declared BEFORE /devices/{ident} so it isn't captured as ident='new'.
    """
    now = int(time.time())
    with get_db() as db:
        rows = db.execute(
            """
            SELECT d.* FROM devices d
            WHERE d.label IS NULL AND d.person_id IS NULL AND d.reviewed_unix IS NULL
              AND d.last_seen_unix >= ?
              -- only genuinely ambiguous devices: no name, or a generic one that
              -- doesn't say which device it is. A distinctive hostname (Nest-Mini,
              -- tablo, sauron, Aaron-s-Z-Flip5) already self-identifies -> not here.
              AND (d.hostname IS NULL
                   OR LOWER(d.hostname) IN ('*', 'localhost', 'iphone', 'ipad', 'mac', 'android'))
            ORDER BY d.first_seen_unix DESC
            """,
            (now - NEW_WINDOW,),
        ).fetchall()
        out = []
        for r in rows:
            ip = r["last_ip"]
            domains = [
                x["domain"]
                for x in db.execute(
                    "SELECT domain, COUNT(*) n FROM dns_queries "
                    "WHERE client_ip=? AND ts_unix>=? GROUP BY domain ORDER BY n DESC LIMIT 8",
                    (ip, now - 7 * 86400),
                ).fetchall()
            ]
            d = _device_row(r)
            d["top_domains"] = domains[:5]
            d["guess"] = _guess_identity(r["vendor"], r["hostname"], domains)
            d["is_new"] = bool(r["first_seen_unix"] and r["first_seen_unix"] >= now - 7 * 86400)
            out.append(d)
    return out


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


# ── New-devices review inbox ────────────────────────────────────────────────

NEW_WINDOW = 30 * 86400  # seen within this = still relevant to review

# (substrings in the DNS fingerprint) -> suggested identity, first match wins.
_DNS_RULES: list[tuple[tuple[str, ...], str]] = [
    (("unagi", "a2z.com", "alexa"), "Amazon Echo / Alexa"),
    (("wyzecam", "wyze"), "Wyze camera"),
    (("vizio", "vzolab", "tvinteractive"), "Vizio TV"),
    (("roku",), "Roku TV / player"),
    (("iphone-ld", "guzzoni.apple"), "iPhone"),
    (("ipados", "ipad"), "iPad"),
    (("appletv", "tv.apple", "apple-tv"), "Apple TV"),
    (("icloud", "aaplimg", "apple.com"), "Apple device"),
    (("googlecast", "nest.com", "clients3.google"), "Google / Nest device"),
    (("tplinkra", "kasa", "tplink"), "TP-Link / Kasa device"),
    (("miio", "xiaomi"), "Xiaomi / Mi device"),
    (("nextdoor", "tiktok", "snapchat"), "Phone (guest?)"),
]


_GENERIC_TYPES = {"iphone": "iPhone", "ipad": "iPad", "mac": "Mac", "android": "Android device"}


def _guess_identity(vendor: str | None, hostname: str | None, domains: list[str]) -> str:
    """Best-guess identity: a distinctive name, else a generic device-type, else the
    DNS fingerprint, else the OUI vendor."""
    h = (hostname or "").strip()
    hl = h.lower()
    if h and hl not in ("*", "localhost") and hl not in _GENERIC_TYPES:
        return h  # the device advertised a real, distinctive name
    if hl in _GENERIC_TYPES:
        return _GENERIC_TYPES[hl]  # "iPad" / "Mac" -- a useful type, still needs a person
    blob = " ".join(domains).lower()
    for keys, label in _DNS_RULES:
        if any(k in blob for k in keys):
            return label
    v = (vendor or "").strip()
    if v and v.lower() != "locally administered":
        return f"{v} device"
    if v.lower() == "locally administered":
        return "Phone / mobile (randomized MAC)"
    return "Unknown device"


class DismissBody(BaseModel):
    dismissed: bool = True


@router.put("/devices/{ident}/dismiss")
def dismiss_device(ident: str, body: DismissBody):
    """Mark a device reviewed/known (a guest you acknowledge) — clears it from the
    New inbox and protects it from the randomized-MAC prune."""
    with get_db_rw() as db:
        mac = _resolve_mac(db, ident)
        if mac is None:
            raise HTTPException(status_code=404, detail="Device not found")
        db.execute(
            "UPDATE devices SET reviewed_unix = ?, updated_unix = ? WHERE mac = ?",
            (int(time.time()) if body.dismissed else None, int(time.time()), mac),
        )
    return {"mac": mac, "dismissed": body.dismissed}
