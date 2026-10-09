from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import DB_PATH
from api.routes import (
    stats, devices, dns, flows, activity, security, internet, people, reboot,
    jellyfin, domains,
)

app = FastAPI(title="LANtern API", version="0.1.0")


@app.on_event("startup")
def _ensure_schema() -> None:
    """Provision any missing tables (e.g. deco_reboot_events) so the write
    endpoints work even if their collector container hasn't booted yet."""
    from lantern.common import connect_db

    connect_db(DB_PATH).close()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stats.router, prefix="/api")
app.include_router(devices.router, prefix="/api")
app.include_router(dns.router, prefix="/api")
app.include_router(flows.router, prefix="/api")
app.include_router(activity.router, prefix="/api")
app.include_router(security.router, prefix="/api")
app.include_router(internet.router, prefix="/api")
app.include_router(people.router, prefix="/api")
app.include_router(reboot.router, prefix="/api")
app.include_router(jellyfin.router, prefix="/api")
app.include_router(domains.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
