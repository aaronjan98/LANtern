"""IP enrichment for external visitors surfaced on the domain-detail page --
ASN/ISP/org, coarse geo, reverse DNS. Looked up on demand and cached in
ip_intel forever (common.py's SCHEMA has the reasoning): an operator clicking
one external IP is not worth burning ip-api.com's free-tier rate limit
(45 req/min) on, let alone re-querying it every page view.

Hard ceiling, by design of what any IP-based lookup can ever tell you: this is
ISP/carrier-and-coarse-geo, never a person. A mobile carrier IP especially can
resolve to a regional gateway, not the phone's actual location. Never present
this as more certain than that.
"""

import ipaddress
import socket
import time
import urllib.error
import urllib.request
import json

from fastapi import APIRouter, HTTPException

from api.db import get_db, get_db_rw

router = APIRouter()

IP_API_URL = "http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,region,regionName,city,lat,lon,timezone,isp,org,as"


def _is_private(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _reverse_dns(ip: str, timeout: float = 2.0) -> str | None:
    socket.setdefaulttimeout(timeout)
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return None
    finally:
        socket.setdefaulttimeout(None)


def _fetch_ip_api(ip: str, timeout: float = 5.0) -> dict:
    """Raises on network failure; returns the parsed JSON (including
    ip-api's own {"status":"fail", "message": "..."} shape) otherwise."""
    with urllib.request.urlopen(IP_API_URL.format(ip=ip), timeout=timeout) as r:
        return json.loads(r.read())


def _lookup_and_store(ip: str) -> dict:
    now = int(time.time())
    if _is_private(ip):
        row = {
            "ip": ip, "asn": None, "org": None, "isp": None, "city": None,
            "region": None, "country": None, "country_code": None,
            "lat": None, "lon": None, "timezone": None, "ptr_hostname": None,
            "is_private": 1, "error": None, "looked_up_unix": now,
        }
    else:
        ptr = _reverse_dns(ip)
        try:
            data = _fetch_ip_api(ip)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            data = {"status": "fail", "message": str(e)}
        if data.get("status") != "success":
            row = {
                "ip": ip, "asn": None, "org": None, "isp": None, "city": None,
                "region": None, "country": None, "country_code": None,
                "lat": None, "lon": None, "timezone": None, "ptr_hostname": ptr,
                "is_private": 0, "error": data.get("message", "lookup failed"),
                "looked_up_unix": now,
            }
        else:
            row = {
                "ip": ip,
                "asn": data.get("as"),
                "org": data.get("org"),
                "isp": data.get("isp"),
                "city": data.get("city"),
                "region": data.get("regionName"),
                "country": data.get("country"),
                "country_code": data.get("countryCode"),
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "timezone": data.get("timezone"),
                "ptr_hostname": ptr,
                "is_private": 0,
                "error": None,
                "looked_up_unix": now,
            }
    with get_db_rw() as db:
        db.execute(
            """
            INSERT INTO ip_intel
                (ip, asn, org, isp, city, region, country, country_code,
                 lat, lon, timezone, ptr_hostname, is_private, error, looked_up_unix)
            VALUES (:ip, :asn, :org, :isp, :city, :region, :country, :country_code,
                    :lat, :lon, :timezone, :ptr_hostname, :is_private, :error, :looked_up_unix)
            ON CONFLICT(ip) DO UPDATE SET
                asn=excluded.asn, org=excluded.org, isp=excluded.isp, city=excluded.city,
                region=excluded.region, country=excluded.country, country_code=excluded.country_code,
                lat=excluded.lat, lon=excluded.lon, timezone=excluded.timezone,
                ptr_hostname=excluded.ptr_hostname, is_private=excluded.is_private,
                error=excluded.error, looked_up_unix=excluded.looked_up_unix
            """,
            row,
        )
    return row


@router.get("/ip/{ip}/intel")
def ip_intel(ip: str, refresh: bool = False):
    """Cached ASN/ISP/org + coarse geo + reverse DNS. Set refresh=true to
    force a fresh lookup (e.g. if an earlier attempt errored)."""
    with get_db() as db:
        row = db.execute("SELECT * FROM ip_intel WHERE ip = ?", (ip,)).fetchone()
    if row is not None and not refresh:
        return dict(row)
    try:
        return _lookup_and_store(ip)
    except Exception:
        raise HTTPException(503, "IP lookup failed; try again shortly")


@router.get("/ip/{ip}/domains")
def ip_domains(ip: str, hours: int = 8760):
    """Every vhost this IP has hit on your server, with counts -- the
    cross-domain picture an OSINT view needs ("what else has this visitor
    touched"), not just the one domain you clicked in from."""
    cutoff = int(time.time()) - hours * 3600
    with get_db() as db:
        rows = db.execute(
            "SELECT host, COUNT(*) requests, MIN(ts_unix) first_seen, MAX(ts_unix) last_seen "
            "FROM http_requests WHERE client_ip = ? AND ts_unix >= ? AND host IS NOT NULL "
            "GROUP BY host ORDER BY requests DESC",
            (ip, cutoff),
        ).fetchall()
    return [dict(r) for r in rows]
