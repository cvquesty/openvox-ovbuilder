"""
Terraform driver: apply with **per-VM isolated local state**.

=============================================================================
CRITICAL DESIGN: STATE ISOLATION
=============================================================================
The Terraform root always declares a single resource address:

    module.vm → vsphere_virtual_machine.*

If every build shared one terraform.tfstate, changing vm_name from ovca2
to ovca3 was an *in-place update* (vSphere rename), not a create.

Fix: each hostname gets its own state file:

    $XDG_DATA_HOME/ovbuilder/tfstate/<sanitized-hostname>/terraform.tfstate

Vars (including maps like guestinfo_extra_config) are written to a JSON
var-file next to that state (mode 0600) because CLI ``-var=map`` quoting
is fragile for base64 guestinfo payloads.

Legacy shared state in the module directory is migrated once on apply.

=============================================================================
PARALLEL BUILDS
=============================================================================
Every apply runs in a private copy of the root module
(``tfstate/<hostname>/module``) so ``terraform init`` never rewrites a lock
file or backend override that another job is using. An exclusive per-hostname
file lock (``tfstate/<hostname>/.ovbuilder.lock``) is held for init + apply:
a second build of the same hostname fails fast instead of racing on the var
file and state. Terraform's own state lock (``-lock-timeout``) still guards
against anything else touching that state.

Optional remote backends (``terraform_backend`` = s3 / azurerm / gcs /
consul) store each VM under ``<terraform_state_prefix>/<hostname>``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

from rich.console import Console

from .config import OvbuilderConfig, get_config_manager

console = Console()

# Filesystem-safe subset for state directory names (hostnames may be FQDNs).
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_state_key(vm_name: str) -> str:
    """
    Map a VM hostname to a safe single path component.

    Replaces disallowed characters with underscore; caps length at 128.
    """
    key = (vm_name or "unnamed").strip()
    key = _SAFE_NAME.sub("_", key)
    key = key.strip("._-") or "unnamed"
    return key[:128]


def state_dir_for_vm(vm_name: str, data_dir: Optional[Path] = None) -> Path:
    """Return (and create) the per-VM state directory under XDG data."""
    if data_dir is None:
        data_dir = get_config_manager().data_dir
    path = Path(data_dir) / "tfstate" / sanitize_state_key(vm_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_file_for_vm(vm_name: str, data_dir: Optional[Path] = None) -> Path:
    """Path to terraform.tfstate for this hostname."""
    return state_dir_for_vm(vm_name, data_dir=data_dir) / "terraform.tfstate"


class StateLockedError(RuntimeError):
    """Another ovbuilder process is already building this hostname."""


@contextlib.contextmanager
def hostname_lock(state_dir: Path) -> Iterator[None]:
    """
    Hold an exclusive, non-blocking lock on ``state_dir`` for one build.

    The OS releases it if the process dies, so a crashed build never leaves
    a stale lock behind.
    """
    lock_path = state_dir / ".ovbuilder.lock"
    fh = open(lock_path, "a+")
    try:
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise StateLockedError(
                f"Another build is already running for {state_dir.name} "
                f"(lock {lock_path}). Wait for it to finish or cancel it."
            ) from exc
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            else:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        fh.close()


# Copied module must not carry provider caches or state from the source dir.
_WORKDIR_IGNORE = shutil.ignore_patterns(
    ".terraform", "*.tfstate", "*.tfstate.*", "crash.log", ".git"
)


def prepare_workdir(tf_dir: Path, state_dir: Path) -> Path:
    """Refresh ``state_dir/module`` from ``tf_dir``; caller holds the hostname lock."""
    workdir = state_dir / "module"
    if workdir.exists():
        shutil.rmtree(workdir)
    shutil.copytree(tf_dir, workdir, ignore=_WORKDIR_IGNORE)
    return workdir


# Backend → (argument that selects the state object, value template).
_REMOTE_BACKENDS: Dict[str, tuple[str, str]] = {
    "s3": ("key", "{prefix}/{name}/terraform.tfstate"),
    "azurerm": ("key", "{prefix}/{name}/terraform.tfstate"),
    "gcs": ("prefix", "{prefix}/{name}"),
    "consul": ("path", "{prefix}/{name}"),
}
SUPPORTED_BACKENDS = ("local",) + tuple(_REMOTE_BACKENDS)
BACKEND_OVERRIDE_FILE = "ovbuilder_backend_override.tf"


def _hcl_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))


def write_remote_backend(
    workdir: Path,
    state_dir: Path,
    backend: str,
    settings: Dict[str, Any],
    prefix: str,
    vm_name: str,
) -> Path:
    """
    Point the working copy at a remote backend for this VM.

    Writes a ``*_override.tf`` (replaces the root ``backend "local"`` block)
    and a 0600 ``backend.hcl`` passed via ``-backend-config=`` so nothing
    lands on argv.
    """
    key_arg, template = _REMOTE_BACKENDS[backend]
    (workdir / BACKEND_OVERRIDE_FILE).write_text(
        f'terraform {{\n  backend "{backend}" {{}}\n}}\n', encoding="utf-8"
    )
    merged = dict(settings or {})
    merged[key_arg] = template.format(
        prefix=(prefix or "ovbuilder").strip("/"), name=sanitize_state_key(vm_name)
    )
    lines = [f"{k} = {_hcl_value(v)}" for k, v in sorted(merged.items())]
    backend_file = state_dir / "backend.hcl"
    _write_var_file_text(backend_file, "\n".join(lines) + "\n")
    return backend_file


def push_local_state(workdir: Path, env: dict, local_state: Path) -> bool:
    """
    One-time copy of per-VM local state into the remote backend.

    ``terraform state push`` (no ``-force``) refuses lineage/serial mismatches,
    so an existing remote state is never overwritten.
    """
    console.print(f"[cyan]Pushing local state {local_state} to remote backend…[/cyan]")
    try:
        subprocess.run(
            ["terraform", "state", "push", str(local_state)],
            cwd=workdir,
            env=env,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        console.print(
            f"[red]terraform state push failed (exit {e.returncode}); "
            f"local state left at {local_state}.[/red]"
        )
        return False
    local_state.rename(local_state.with_suffix(".tfstate.migrated"))
    return True


def _vm_name_from_state_file(state_path: Path) -> Optional[str]:
    """
    Best-effort read of vsphere_virtual_machine.name from a local state file.

    Used only for migrating the old shared state into the correct per-VM path.
    """
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        for resource in data.get("resources") or []:
            if resource.get("type") != "vsphere_virtual_machine":
                continue
            for inst in resource.get("instances") or []:
                name = (inst.get("attributes") or {}).get("name")
                if name:
                    return str(name)
    except Exception:
        return None
    return None


def migrate_legacy_shared_state(tf_dir: Path) -> None:
    """
    One-time move of module-dir terraform.tfstate → per-VM path.

    Older ovbuilder versions wrote a single shared state under the Terraform
    module directory. Leaving it there is dangerous if someone runs bare
    ``terraform apply`` without a per-VM backend path. Migration renames it
    into XDG.
    """
    legacy = tf_dir / "terraform.tfstate"
    if not legacy.is_file():
        return
    try:
        # Empty / stub files are not worth migrating.
        if legacy.stat().st_size < 50:
            return
    except OSError:
        return

    vm_name = _vm_name_from_state_file(legacy)
    if not vm_name:
        console.print(
            f"[yellow]Legacy state at {legacy} has no VM name; "
            f"leave it for manual review.[/yellow]"
        )
        return

    dest = state_file_for_vm(vm_name)
    if dest.exists():
        # Per-VM state already authoritative — quarantine shared file.
        aside = legacy.with_suffix(".tfstate.legacy-shared")
        try:
            legacy.rename(aside)
            backup = tf_dir / "terraform.tfstate.backup"
            if backup.exists():
                backup.rename(backup.with_suffix(".backup.legacy-shared"))
            console.print(
                f"[dim]Legacy shared state set aside as {aside.name} "
                f"(per-VM state already exists for {vm_name}).[/dim]"
            )
        except OSError as exc:
            console.print(f"[yellow]Could not set aside legacy state: {exc}[/yellow]")
        return

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        legacy.rename(dest)
        backup = tf_dir / "terraform.tfstate.backup"
        if backup.exists():
            backup.rename(dest.with_suffix(".tfstate.backup"))
        console.print(
            f"[green]Migrated legacy shared Terraform state → {dest}[/green]\n"
            f"[dim]Tracked VM: {vm_name}.[/dim]"
        )
    except OSError as exc:
        console.print(f"[yellow]Could not migrate legacy state: {exc}[/yellow]")


def ensure_terraform_init(
    tf_dir: Path,
    env: dict,
    *,
    state_path: Optional[Path] = None,
    backend_config_file: Optional[Path] = None,
    force_reconfigure: bool = False,
) -> bool:
    """
    Run ``terraform init`` with the backend for this VM.

    Local backend: ``path=`` (not deprecated ``-state=``). Remote backend:
    ``backend_config_file`` from :func:`write_remote_backend`. Either way
    ``-reconfigure`` is passed so a reused TF_DATA_DIR never keeps a stale
    backend. Providers land under TF_DATA_DIR when set.
    """
    terraform_meta = Path(env.get("TF_DATA_DIR") or (tf_dir / ".terraform"))
    needs_init = force_reconfigure or not (
        terraform_meta.is_dir() and any(terraform_meta.iterdir())
    )
    # Backend must be applied whenever we target a specific state.
    if state_path is not None or backend_config_file is not None:
        needs_init = True

    if not needs_init:
        return True

    console.print(f"[dim]terraform init in {tf_dir}…[/dim]")
    cmd = ["terraform", "init", "-input=false", "-upgrade=false"]
    if backend_config_file is not None:
        cmd.append("-reconfigure")
        cmd.append(f"-backend-config={backend_config_file}")
    elif state_path is not None:
        cmd.append("-reconfigure")
        cmd.append(f"-backend-config=path={state_path}")
    try:
        subprocess.run(cmd, cwd=tf_dir, env=env, check=True)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]terraform init failed (exit {e.returncode})[/red]")
        return False


def _write_var_file_text(path: Path, text: str) -> None:
    """Write a file with 0600 permissions (may contain passwords)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _write_var_file(path: Path, payload: dict) -> None:
    """Write JSON var-file with restrictive permissions (may contain passwords)."""
    _write_var_file_text(path, json.dumps(payload, indent=2) + "\n")


def run_terraform_apply(
    tf_dir: Path,
    vars: Dict,
    config: OvbuilderConfig,
    vsphere_user: Optional[str] = None,
    vsphere_password: Optional[str] = None,
) -> bool:
    """
    Run ``terraform apply -auto-approve`` for one VM.

    Parameters
    ----------
    tf_dir : Path
        Root module (contains main.tf + modules/vm).
    vars : dict
        Build-time values from interview (vm_name, networks, guestinfo, …).
    config : OvbuilderConfig
        Fallbacks for placement / firmware / domain.
    vsphere_user / vsphere_password : optional explicit auth

    Returns
    -------
    bool
        True if terraform exited 0.
    """
    if not (tf_dir / "main.tf").exists():
        console.print(f"[red]No Terraform root module found at {tf_dir}[/red]")
        return False

    backend = (getattr(config, "terraform_backend", "") or "local").strip().lower()
    if backend not in SUPPORTED_BACKENDS:
        console.print(
            f"[red]Unsupported terraform_backend {backend!r}; "
            f"use one of: {', '.join(SUPPORTED_BACKENDS)}[/red]"
        )
        return False

    vm_name = vars.get("vm_name") or "unnamed"
    sdir = state_dir_for_vm(vm_name)
    try:
        sdir.chmod(0o700)
    except OSError:
        pass
    try:
        with hostname_lock(sdir):
            return _apply_locked(
                tf_dir, sdir, vm_name, backend, vars, config,
                vsphere_user, vsphere_password,
            )
    except StateLockedError as exc:
        console.print(f"[red]{exc}[/red]")
        return False


def _apply_locked(
    tf_dir: Path,
    sdir: Path,
    vm_name: str,
    backend: str,
    vars: Dict,
    config: OvbuilderConfig,
    vsphere_user: Optional[str],
    vsphere_password: Optional[str],
) -> bool:
    """Init + apply in this VM's working copy; caller holds the hostname lock."""
    var_file = sdir / "ovbuilder.auto.tfvars.json"
    workdir = prepare_workdir(tf_dir, sdir)

    env = os.environ.copy()
    # Per-VM plugin/backend metadata (providers + backend config).
    env["TF_DATA_DIR"] = str(sdir / ".terraform")

    effective_user = vsphere_user or vars.get("vsphere_user")
    effective_password = vsphere_password or vars.get("vsphere_password")
    effective_server = vars.get("vsphere_server") or getattr(
        config, "vsphere_server", None
    )

    # Provider auth via both TF_VAR_* and VSPHERE_* (belt and suspenders).
    if effective_user:
        env["TF_VAR_vsphere_user"] = str(effective_user)
        env["VSPHERE_USER"] = str(effective_user)
    if effective_password:
        env["TF_VAR_vsphere_password"] = str(effective_password)
        env["VSPHERE_PASSWORD"] = str(effective_password)
    if effective_server:
        env["TF_VAR_vsphere_server"] = str(effective_server)
        env["VSPHERE_SERVER"] = str(effective_server)

    migrate_legacy_shared_state(tf_dir)
    # Re-resolve after possible migration for this hostname.
    state_path = state_file_for_vm(vm_name)

    if backend == "local":
        if not ensure_terraform_init(workdir, env, state_path=state_path):
            return False
        state_label = str(state_path)
    else:
        backend_file = write_remote_backend(
            workdir,
            sdir,
            backend,
            getattr(config, "terraform_backend_config", None) or {},
            getattr(config, "terraform_state_prefix", "") or "ovbuilder",
            vm_name,
        )
        if not ensure_terraform_init(workdir, env, backend_config_file=backend_file):
            return False
        if state_path.exists() and not push_local_state(workdir, env, state_path):
            return False
        state_label = f"{backend} backend"

    # Normalize CLI "golden" → Terraform "clone".
    provision_mode = vars.get("provision_mode") or "clone"
    if provision_mode == "golden":
        provision_mode = "clone"

    allow_insecure = bool(
        getattr(config, "vsphere_allow_unverified_ssl", True)
    )

    tf_payload = {
        "vsphere_user": effective_user or "",
        "vsphere_password": effective_password or "",
        "vsphere_server": effective_server or config.vsphere_server,
        "vsphere_allow_unverified_ssl": allow_insecure,
        "vsphere_datacenter": vars.get("datacenter", config.datacenter),
        "vsphere_cluster": vars.get("cluster", config.cluster),
        "vm_datastore": vars.get("vm_datastore", config.vm_datastore),
        "vm_datastore_cluster": vars.get(
            "vm_datastore_cluster",
            getattr(config, "vm_datastore_cluster", ""),
        )
        or "",
        "iso_datastore": vars.get("iso_datastore", config.iso_datastore),
        "vm_name": vm_name,
        "iso_path": vars.get("iso_path") or "",
        "provision_mode": provision_mode,
        "template_name": vars.get("template_name") or "",
        "template_datacenter": vars.get("template_datacenter") or "",
        "compute_type": vars.get("compute_type") or "cluster",
        "guest_id": vars.get("guest_id") or "",
        "guestinfo_extra_config": vars.get("guestinfo_extra_config") or {},
        "num_cpus": int(vars.get("num_cpus", config.default_cpus)),
        "memory_mb": int(vars.get("memory_mb", config.default_memory_gb * 1024)),
        "disk_size_gb": int(vars.get("disk_size_gb", config.default_disk_gb)),
        "networks": vars.get("networks") or config.networks,
        "domain": config.domain,
        "folder": config.folder,
        "firmware": config.firmware,
    }
    _write_var_file(var_file, tf_payload)

    lock_timeout = getattr(config, "terraform_lock_timeout", "") or "60s"
    # No -state / -state-out / -backup — the backend owns the state.
    cmd = [
        "terraform",
        "apply",
        "-auto-approve",
        "-input=false",
        "-lock=true",
        f"-lock-timeout={lock_timeout}",
        f"-var-file={var_file}",
    ]

    console.print(f"[cyan]Running Terraform in {workdir} (copy of {tf_dir})[/cyan]")
    console.print(f"[dim]Mode: {provision_mode} | State: {state_label}[/dim]")
    if provision_mode == "clone":
        console.print(f"[dim]Template: {tf_payload.get('template_name')}[/dim]")
    if backend == "local":
        if state_path.exists():
            console.print(
                f"[dim]Existing state for [bold]{vm_name}[/bold] — update that VM only.[/dim]"
            )
        else:
            console.print(f"[dim]New state for [bold]{vm_name}[/bold] — create.[/dim]")

    legacy = tf_dir / "terraform.tfstate"
    if legacy.exists():
        console.print(
            f"[yellow]Note: leftover shared state at {legacy} "
            f"is ignored for new builds.[/yellow]"
        )

    try:
        subprocess.run(cmd, cwd=workdir, env=env, check=True)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Terraform exited with code {e.returncode}[/red]")
        return False
