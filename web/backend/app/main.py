"""FastAPI entrypoint: `uvicorn app.main:app --reload`."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import auth as auth_api
from .api import builds as builds_api
from .api import inventory as inventory_api
from .api import vms as vms_api
from .api import settings as settings_api
from .config import get_settings
from .database import active_job_counts, init_db
from .models import BuildStatus

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_api.router, prefix="/api/auth", tags=["auth"])
app.include_router(builds_api.router, prefix="/api/builds", tags=["builds"])
app.include_router(inventory_api.router, prefix="/api/inventory", tags=["inventory"])
app.include_router(vms_api.router, prefix="/api/vms", tags=["vms"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])


@app.get("/api/health")
async def health():
    payload = {
        "status": "ok",
        "app": settings.app_name,
        "db_ok": False,
        "queued": None,
        "running": None,
        "active": None,
        "max_concurrent_builds": settings.max_concurrent_builds,
        "max_queue_depth": settings.max_queue_depth,
    }
    try:
        counts = await active_job_counts()
        queued = counts.get(BuildStatus.queued.value, 0)
        running = counts.get(BuildStatus.running.value, 0)
        payload.update(
            db_ok=True,
            queued=queued,
            running=running,
            active=queued + running,
        )
    except Exception:  # noqa: BLE001 — liveness still 200; db_ok tells the truth
        payload["status"] = "degraded"
    return payload
