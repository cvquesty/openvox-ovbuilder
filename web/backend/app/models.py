"""Pydantic models shared by the API and Celery tasks."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Role(str, Enum):
    admin = "admin"
    builder = "builder"
    viewer = "viewer"


class BuildRequest(BaseModel):
    hostname: str = Field(..., min_length=1, max_length=63)
    ip: str
    os_image: str = Field(..., description="e.g. ubuntu-24.04, almalinux-10")
    prefix: str = "24"
    cpus: int = 2
    memory_gb: int = 4
    disk_gb: int = 80
    gateway: Optional[str] = None
    dns: list[str] = Field(default_factory=list)
    environment: str = Field("dev", description="dev | prod — maps to a datastore cluster")
    cluster: Optional[str] = Field(None, description="Compute cluster from live inventory")
    network: Optional[str] = Field(None, description="Port group / network from live inventory")
    location: Optional[str] = None
    vsphere_server: Optional[str] = None
    vsphere_user: Optional[str] = None
    vsphere_password: Optional[str] = None
    skip_dnf_groups: bool = False


class BuildStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelling = "cancelling"
    cancelled = "cancelled"


class BuildJob(BaseModel):
    id: str
    status: BuildStatus = BuildStatus.queued
    request: BuildRequest
    requested_by: str
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    celery_task_id: Optional[str] = None
    log_tail: str = ""
    error: Optional[str] = None
    vm_ip: Optional[str] = None
    vm_name: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    username: str


class UserOut(BaseModel):
    username: str
    role: Role
    display_name: Optional[str] = None
    email: Optional[str] = None
