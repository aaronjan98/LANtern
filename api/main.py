from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import stats, devices, dns, flows

app = FastAPI(title="LANtern API", version="0.1.0")

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


@app.get("/api/health")
def health():
    return {"status": "ok"}
