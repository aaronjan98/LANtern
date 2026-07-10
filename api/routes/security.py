"""Security endpoints — SSH auth activity and fail2ban effectiveness.

Reads ssh_events / fail2ban_events (fed by lantern.security_ingest). The headline
question is "is fail2ban working?": /security/summary puts attack attempts next to
bans so you can see whether the attackers actually got caught.
"""

import time
from fastapi import APIRouter, Query
from api.db import get_db

router = APIRouter()

ATTACK_EVENTS = ("failed", "invalid", "preauth")
BAN_ACTIONS = ("Ban", "Increase Ban", "Restore Ban")


@router.get("/security/summary")
def security_summary(hours: int = Query(default=24, ge=1, le=720)):
    """Attack attempts vs. bans over the window — the fail2ban health check."""
    cutoff = int(time.time()) - hours * 3600
    with get_db() as db:
        attackers = [
            r["src_ip"]
            for r in db.execute(
                f"SELECT DISTINCT src_ip FROM ssh_events "
                f"WHERE event IN ({','.join('?' * len(ATTACK_EVENTS))}) "
                f"AND ts_unix >= ? AND src_ip IS NOT NULL",
                (*ATTACK_EVENTS, cutoff),
            ).fetchall()
        ]
        attempts = db.execute(
            f"SELECT COUNT(*) n FROM ssh_events "
            f"WHERE event IN ({','.join('?' * len(ATTACK_EVENTS))}) AND ts_unix >= ?",
            (*ATTACK_EVENTS, cutoff),
        ).fetchone()["n"]
        banned = [
            r["ip"]
            for r in db.execute(
                f"SELECT DISTINCT ip FROM fail2ban_events "
                f"WHERE action IN ({','.join('?' * len(BAN_ACTIONS))}) "
                f"AND ts_unix >= ? AND ip IS NOT NULL",
                (*BAN_ACTIONS, cutoff),
            ).fetchall()
        ]
        bans = db.execute(
            f"SELECT COUNT(*) n FROM fail2ban_events "
            f"WHERE action IN ({','.join('?' * len(BAN_ACTIONS))}) AND ts_unix >= ?",
            (*BAN_ACTIONS, cutoff),
        ).fetchone()["n"]
        logins = db.execute(
            "SELECT COUNT(*) n FROM ssh_events WHERE event = 'accepted' AND ts_unix >= ?",
            (cutoff,),
        ).fetchone()["n"]

    attacker_set = set(attackers)
    banned_set = set(banned)
    caught = attacker_set & banned_set
    coverage = round(100 * len(caught) / len(attacker_set), 1) if attacker_set else None
    # Attackers with real repeat activity that were never banned = the gap worth seeing.
    return {
        "hours": hours,
        "attempts": attempts,
        "attackers": len(attacker_set),
        "bans": bans,
        "banned_ips": len(banned_set),
        "logins": logins,
        "caught": len(caught),
        "coverage_pct": coverage,
        "uncaught": sorted(attacker_set - banned_set)[:20],
    }


@router.get("/security/ssh/attackers")
def ssh_attackers(
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=50, le=500),
):
    """Source IPs by failed/invalid attempt volume, with whether fail2ban banned them."""
    cutoff = int(time.time()) - hours * 3600
    ban_ph = ",".join("?" * len(BAN_ACTIONS))
    att_ph = ",".join("?" * len(ATTACK_EVENTS))
    with get_db() as db:
        rows = db.execute(
            f"""
            SELECT
                s.src_ip,
                COUNT(*) AS attempts,
                COUNT(DISTINCT s.username) AS users_tried,
                GROUP_CONCAT(DISTINCT s.username) AS usernames,
                MIN(s.ts_unix) AS first_seen,
                MAX(s.ts_unix) AS last_seen,
                EXISTS(
                    SELECT 1 FROM fail2ban_events f
                    WHERE f.ip = s.src_ip AND f.action IN ({ban_ph}) AND f.ts_unix >= ?
                ) AS banned
            FROM ssh_events s
            WHERE s.event IN ({att_ph}) AND s.ts_unix >= ? AND s.src_ip IS NOT NULL
            GROUP BY s.src_ip
            ORDER BY attempts DESC
            LIMIT ?
            """,
            (*BAN_ACTIONS, cutoff, *ATTACK_EVENTS, cutoff, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["banned"] = bool(d["banned"])
        users = (d.pop("usernames") or "").split(",")
        d["usernames"] = sorted({u for u in users if u})[:12]
        out.append(d)
    return out


@router.get("/security/ssh/logins")
def ssh_logins(
    hours: int = Query(default=168, ge=1, le=2160),
    limit: int = Query(default=50, le=500),
):
    """Successful SSH logins — who got in, from where."""
    cutoff = int(time.time()) - hours * 3600
    with get_db() as db:
        rows = db.execute(
            """
            SELECT ts_unix, username, src_ip, port, method, host
            FROM ssh_events
            WHERE event = 'accepted' AND ts_unix >= ?
            ORDER BY ts_unix DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/security/bans")
def security_bans(
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=100, le=1000),
):
    """Recent fail2ban actions (newest first)."""
    cutoff = int(time.time()) - hours * 3600
    with get_db() as db:
        rows = db.execute(
            """
            SELECT ts_unix, jail, action, ip, host
            FROM fail2ban_events
            WHERE ts_unix >= ?
            ORDER BY ts_unix DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
    return [dict(r) for r in rows]
