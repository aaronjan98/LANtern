import time

from fastapi import APIRouter

from api.db import get_db

router = APIRouter()


@router.get("/internet/speedtest")
def speedtest_history(hours: int = 168, limit: int = 500):
    """Speedtest rows (newest first) within the window, successes and failures."""
    since = int(time.time()) - hours * 3600
    with get_db() as db:
        rows = db.execute(
            """
            SELECT ts_unix, ok, download_bps, upload_bps, ping_ms, jitter_ms,
                   packet_loss, server_name, isp, result_url, error
            FROM speedtest_results
            WHERE ts_unix >= ?
            ORDER BY ts_unix DESC
            LIMIT ?
            """,
            (since, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/internet/speedtest/latest")
def speedtest_latest():
    """The most recent *successful* test, for the headline cards."""
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM speedtest_results WHERE ok = 1 "
            "ORDER BY ts_unix DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def _derive_outages(rows):
    """Collapse contiguous up=0 checks into outage windows.

    rows must be ordered oldest-first. Each outage carries the recovery time
    (end), duration, whether it's ongoing, and wan_only -- True when the LAN
    gateway stayed reachable throughout, i.e. 'connected but no internet'.
    """
    outages = []
    cur = None
    for r in rows:
        if not r["up"]:
            gw_up = bool(r["gateway_up"])
            if cur is None:
                cur = {"start_ts": r["ts_unix"], "last_down_ts": r["ts_unix"], "wan_only": gw_up}
            else:
                cur["last_down_ts"] = r["ts_unix"]
                cur["wan_only"] = cur["wan_only"] and gw_up
        elif cur is not None:
            cur["end_ts"] = r["ts_unix"]
            cur["ongoing"] = False
            cur["duration_s"] = cur["end_ts"] - cur["start_ts"]
            outages.append(cur)
            cur = None
    if cur is not None:
        cur["end_ts"] = None
        cur["ongoing"] = True
        cur["duration_s"] = int(time.time()) - cur["start_ts"]
        outages.append(cur)
    for o in outages:
        o.pop("last_down_ts", None)
    return outages


@router.get("/internet/outages")
def internet_outages(hours: int = 720, limit: int = 100):
    """Derived outage windows (newest first) within the window."""
    since = int(time.time()) - hours * 3600
    with get_db() as db:
        rows = db.execute(
            "SELECT ts_unix, up, gateway_up FROM wan_checks "
            "WHERE ts_unix >= ? ORDER BY ts_unix ASC",
            (since,),
        ).fetchall()
    outages = _derive_outages(rows)
    outages.reverse()
    return outages[:limit]


@router.get("/internet/connectivity/summary")
def connectivity_summary(hours: int = 168):
    """Uptime %, outage count, total downtime and current status for the window."""
    since = int(time.time()) - hours * 3600
    with get_db() as db:
        rows = db.execute(
            "SELECT ts_unix, up, gateway_up FROM wan_checks "
            "WHERE ts_unix >= ? ORDER BY ts_unix ASC",
            (since,),
        ).fetchall()
    checks = len(rows)
    up_checks = sum(1 for r in rows if r["up"])
    outages = _derive_outages(rows)
    downtime_s = sum(o["duration_s"] for o in outages)
    last = rows[-1] if rows else None
    return {
        "hours": hours,
        "checks": checks,
        "up_checks": up_checks,
        "uptime_pct": round(up_checks / checks * 100, 3) if checks else None,
        "outages": len(outages),
        "downtime_s": downtime_s,
        "current_up": (bool(last["up"]) if last else None),
        "current_gateway_up": (bool(last["gateway_up"]) if last and last["gateway_up"] is not None else None),
        "last_check_ts": (last["ts_unix"] if last else None),
    }


@router.get("/internet/latency")
def internet_latency(hours: int = 24, bucket_minutes: int = 30):
    """WAN latency + packet-loss bucketed over time, from the connectivity probe.

    avg/max RTT is over successful probes; loss% is the share of target probes
    that failed in the bucket (1 - targets_ok / targets_total).
    """
    since = int(time.time()) - hours * 3600
    bucket_s = max(1, bucket_minutes) * 60
    with get_db() as db:
        rows = db.execute(
            """
            SELECT (ts_unix / ?) * ? AS bucket,
                   AVG(rtt_ms) AS avg_rtt,
                   MAX(rtt_ms) AS max_rtt,
                   SUM(targets_ok) AS ok,
                   SUM(targets_total) AS tot,
                   COUNT(*) AS samples
            FROM wan_checks
            WHERE ts_unix >= ?
            GROUP BY bucket
            ORDER BY bucket ASC
            """,
            (bucket_s, bucket_s, since),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        tot = d.pop("tot") or 0
        ok = d.pop("ok") or 0
        d["loss_pct"] = round((1 - ok / tot) * 100, 2) if tot else None
        d["avg_rtt"] = round(d["avg_rtt"], 2) if d["avg_rtt"] is not None else None
        out.append(d)
    return out


@router.get("/internet/kuma/monitors")
def kuma_monitors(hours: int = 168):
    """Per-monitor current status, latest ping and uptime% over the window."""
    since = int(time.time()) - hours * 3600
    with get_db() as db:
        rows = db.execute(
            """
            SELECT m.id, m.name, m.type,
                   (SELECT status FROM kuma_heartbeats WHERE monitor_id = m.id
                      ORDER BY ts_unix DESC LIMIT 1) AS status,
                   (SELECT ping_ms FROM kuma_heartbeats WHERE monitor_id = m.id
                      ORDER BY ts_unix DESC LIMIT 1) AS ping_ms,
                   (SELECT ts_unix FROM kuma_heartbeats WHERE monitor_id = m.id
                      ORDER BY ts_unix DESC LIMIT 1) AS last_ts,
                   SUM(CASE WHEN h.status = 1 THEN 1 ELSE 0 END) AS up_ct,
                   SUM(CASE WHEN h.status IN (0, 1) THEN 1 ELSE 0 END) AS tot_ct
            FROM kuma_monitors m
            LEFT JOIN kuma_heartbeats h
              ON h.monitor_id = m.id AND h.ts_unix >= ?
            WHERE m.active = 1
            GROUP BY m.id
            ORDER BY m.name COLLATE NOCASE
            """,
            (since,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        tot = d.pop("tot_ct") or 0
        up = d.pop("up_ct") or 0
        d["uptime_pct"] = round(up / tot * 100, 2) if tot else None
        out.append(d)
    return out


@router.get("/internet/kuma/events")
def kuma_events(hours: int = 168, limit: int = 50):
    """Recent important heartbeats (up/down transitions) across all monitors."""
    since = int(time.time()) - hours * 3600
    with get_db() as db:
        rows = db.execute(
            """
            SELECT h.ts_unix, h.status, h.ping_ms, h.msg, m.name
            FROM kuma_heartbeats h
            JOIN kuma_monitors m ON m.id = h.monitor_id
            WHERE h.important = 1 AND h.ts_unix >= ?
            ORDER BY h.ts_unix DESC
            LIMIT ?
            """,
            (since, limit),
        ).fetchall()
    return [dict(r) for r in rows]
