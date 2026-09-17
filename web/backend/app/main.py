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
from .database import init_db

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
    return {"status": "ok", "app": settings.app_name}
