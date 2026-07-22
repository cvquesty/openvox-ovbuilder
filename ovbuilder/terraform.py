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
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

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
    force_reconfigure: bool = False,
) -> bool:
    """
    Run ``terraform init`` with local backend path for this VM.

    Uses backend ``path=`` (not deprecated ``-state=``). When ``state_path`` is
    set, always passes ``-reconfigure`` so switching between per-VM states in
    the same module directory is safe. Providers land under TF_DATA_DIR when set.
    """
    terraform_meta = Path(env.get("TF_DATA_DIR") or (tf_dir / ".terraform"))
    needs_init = force_reconfigure or not (
        terraform_meta.is_dir() and any(terraform_meta.iterdir())
    )
    # Backend path must be applied whenever we target a specific state file.
    if state_path is not None:
        needs_init = True

    if not needs_init:
        return True

    console.print(f"[dim]terraform init in {tf_dir}…[/dim]")
    cmd = ["terraform", "init", "-input=false", "-upgrade=false"]
    if state_path is not None:
        cmd.append("-reconfigure")
        cmd.append(f"-backend-config=path={state_path}")
    try:
        subprocess.run(cmd, cwd=tf_dir, env=env, check=True)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]terraform init failed (exit {e.returncode})[/red]")
        return False


def _write_var_file(path: Path, payload: dict) -> None:
    """Write JSON var-file with restrictive permissions (may contain passwords)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


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

    vm_name = vars.get("vm_name") or "unnamed"
    sdir = state_dir_for_vm(vm_name)
    state_path = sdir / "terraform.tfstate"
    var_file = sdir / "ovbuilder.auto.tfvars.json"

    env = os.environ.copy()
    # Isolate plugin/backend metadata per VM so concurrent builds do not clobber
    # each other's local backend path in a shared module directory.
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

    # Local backend path (modern replacement for -state=).
    if not ensure_terraform_init(tf_dir, env, state_path=state_path):
        return False

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
        "iso_datastore": vars.get("iso_datastore", config.iso_datastore),
        "vm_name": vm_name,
        "iso_path": vars.get("iso_path") or "",
        "provision_mode": provision_mode,
        "template_name": vars.get("template_name") or "",
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

    # No -state / -state-out / -backup — local backend owns the state file.
    cmd = [
        "terraform",
        "apply",
        "-auto-approve",
        "-input=false",
        f"-var-file={var_file}",
    ]

    console.print(f"[cyan]Running Terraform in {tf_dir}[/cyan]")
    console.print(f"[dim]Mode: {provision_mode} | State: {state_path}[/dim]")
    if provision_mode == "clone":
        console.print(f"[dim]Template: {tf_payload.get('template_name')}[/dim]")
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
        subprocess.run(cmd, cwd=tf_dir, env=env, check=True)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Terraform exited with code {e.returncode}[/red]")
        return False
