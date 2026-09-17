"""vSphere password stays off argv and out of public API payloads."""

from __future__ import annotations

from datetime import datetime, timezone

from app.api.builds import BuildJobPublic
from app.models import BuildJob, BuildRequest, BuildStatus
from app.tasks import _build_command


def _request(**kwargs) -> BuildRequest:
    data = dict(
        hostname="web01",
        ip="10.0.0.10",
        os_image="ubuntu-24.04",
        vsphere_server="vcenter.example.com",
        vsphere_user="svc@example.com",
        vsphere_password="super-secret",
    )
    data.update(kwargs)
    return BuildRequest(**data)


def test_build_command_puts_password_in_env_not_argv():
    req = _request()
    cmd, env = _build_command(req.model_dump(), "job-1")
    assert "--vsphere-password" not in cmd
    assert "super-secret" not in cmd
    assert env["OVBUILDER_VSPHERE_PASSWORD"] == "super-secret"
    assert "--hostname" in cmd
    assert "web01" in cmd
    assert "--vsphere-server" in cmd
    assert "vcenter.example.com" in cmd
    assert "--vsphere-user" in cmd
    assert "svc@example.com" in cmd


def test_build_command_omits_password_env_when_unset():
    req = _request(vsphere_password=None)
    cmd, env = _build_command(req.model_dump(), "job-1")
    assert "--vsphere-password" not in cmd
    assert "OVBUILDER_VSPHERE_PASSWORD" not in env


def test_public_job_schema_nulls_vsphere_password():
    job = BuildJob(
        id="job-1",
        status=BuildStatus.queued,
        request=_request(),
        requested_by="alice",
        created_at=datetime.now(timezone.utc),
    )
    public = BuildJobPublic.from_job(job)
    dumped = public.model_dump()
    assert dumped["request"]["vsphere_password"] is None
    assert "super-secret" not in public.model_dump_json()
    assert job.request.vsphere_password == "super-secret"


def test_build_job_public_openapi_exposes_password_as_nullable():
    """Current staging redacts to null (does not omit the field)."""
    schema = BuildJobPublic.model_json_schema()
    defs = schema.get("$defs") or schema.get("definitions") or {}
    request = defs.get("BuildRequest") or {}
    props = request.get("properties") or {}
    assert "vsphere_password" in props
