"""Activity-oracle endpoints — is the network / homelab idle right now?

Thin HTTP wrapper over lantern.activity. The oracle functions are pure and
read-only (they only SELECT), so we hand them the API's existing read-only
connection. Maintenance jobs (the SnapRAID sync, the Deco WiFi reboot) poll
/api/activity/idle and only run when their guard is true.

/api/activity/verdicts exposes the shadow-mode log (lantern.activity_shadow) so
the thresholds can be eyeballed against real usage before anything is gated.
"""

import json
import sqlite3

from fastapi import APIRouter, Query

from api.db import get_db
from lantern.activity import is_homelab_idle, is_internet_idle

router = APIRouter()


@router.get("/activity/idle")
def activity_idle():
    """Current idle verdict for both signals, plus per-job guards.

    - ``safe_for_snapraid_sync`` — a sync competes with service disk I/O, so it
      only needs the *homelab* to be quiet (internet browsing is irrelevant to
      the array).
    - ``safe_for_deco_reboot`` — a Deco reboot drops *all* WiFi, cutting both
      internet users and anyone reaching services over WiFi, so it needs both
      signals idle. It's the high-stakes job; be conservative.
    """
    with get_db() as db:
        internet_idle, internet_ev = is_internet_idle(db)
        homelab_idle, homelab_ev = is_homelab_idle(db)

    return {
        "internet_idle": internet_idle,
        "homelab_idle": homelab_idle,
        "safe_for_snapraid_sync": homelab_idle,
        "safe_for_deco_reboot": internet_idle and homelab_idle,
        "internet": internet_ev,
        "homelab": homelab_ev,
    }


@router.get("/activity/verdicts")
def activity_verdicts(
    limit: int = Query(100, ge=1, le=1000),
    signal: str | None = Query(None, pattern="^(internet|homelab)$"),
):
    """Recent shadow-mode verdicts, newest first (for validating thresholds)."""
    with get_db() as db:
        try:
            base = (
                "SELECT ts_unix, signal, idle, headline, evidence_json "
                "FROM activity_verdicts"
            )
            params: list = []
            if signal:
                base += " WHERE signal = ?"
                params.append(signal)
            base += " ORDER BY ts_unix DESC LIMIT ?"
            params.append(limit)
            rows = db.execute(base, params).fetchall()
        except sqlite3.OperationalError:
            # Table not created yet (shadow logger hasn't run) — nothing to show.
            return []

    out = []
    for r in rows:
        item = dict(r)
        item["idle"] = bool(item["idle"])
        item["evidence"] = json.loads(item.pop("evidence_json"))
        out.append(item)
    return out
