"""
Terraform driver for ovbuilder.

Per-VM local state under:
  $XDG_DATA_HOME/ovbuilder/tfstate/<vm_name>/terraform.tfstate
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

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_state_key(vm_name: str) -> str:
    key = (vm_name or "unnamed").strip()
    key = _SAFE_NAME.sub("_", key)
    key = key.strip("._-") or "unnamed"
    return key[:128]


def state_dir_for_vm(vm_name: str, data_dir: Optional[Path] = None) -> Path:
    if data_dir is None:
        data_dir = get_config_manager().data_dir
    path = Path(data_dir) / "tfstate" / sanitize_state_key(vm_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_file_for_vm(vm_name: str, data_dir: Optional[Path] = None) -> Path:
    return state_dir_for_vm(vm_name, data_dir=data_dir) / "terraform.tfstate"


def _vm_name_from_state_file(state_path: Path) -> Optional[str]:
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
    legacy = tf_dir / "terraform.tfstate"
    if not legacy.is_file():
        return
    try:
        if legacy.stat().st_size < 50:
            return
    except OSError:
        return

    vm_name = _vm_name_from_state_file(legacy)
    if not vm_name:
        console.print(
            f"[yellow]Legacy state at {legacy} has no VM name; leave it for manual review.[/yellow]"
        )
        return

    dest = state_file_for_vm(vm_name)
    if dest.exists():
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


def ensure_terraform_init(tf_dir: Path, env: dict) -> bool:
    terraform_dir = tf_dir / ".terraform"
    if terraform_dir.is_dir() and any(terraform_dir.iterdir()):
        return True
    console.print(f"[dim]terraform init in {tf_dir}…[/dim]")
    try:
        subprocess.run(
            ["terraform", "init", "-input=false", "-upgrade=false"],
            cwd=tf_dir,
            env=env,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]terraform init failed (exit {e.returncode})[/red]")
        return False


def _write_var_file(path: Path, payload: dict) -> None:
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
    """Execute terraform apply with per-VM state + JSON var-file."""
    if not (tf_dir / "main.tf").exists():
        console.print(f"[red]No Terraform root module found at {tf_dir}[/red]")
        return False

    vm_name = vars.get("vm_name") or "unnamed"
    sdir = state_dir_for_vm(vm_name)
    state_path = sdir / "terraform.tfstate"
    backup_path = sdir / "terraform.tfstate.backup"
    var_file = sdir / "ovbuilder.auto.tfvars.json"

    env = os.environ.copy()

    effective_user = vsphere_user or vars.get("vsphere_user")
    effective_password = vsphere_password or vars.get("vsphere_password")
    effective_server = vars.get("vsphere_server") or getattr(config, "vsphere_server", None)

    if effective_user:
        env["TF_VAR_vsphere_user"] = str(effective_user)
        env["VSPHERE_USER"] = str(effective_user)
    if effective_password:
        env["TF_VAR_vsphere_password"] = str(effective_password)
        env["VSPHERE_PASSWORD"] = str(effective_password)
    if effective_server:
        env["TF_VAR_vsphere_server"] = str(effective_server)
        env["VSPHERE_SERVER"] = str(effective_server)

    if not ensure_terraform_init(tf_dir, env):
        return False

    migrate_legacy_shared_state(tf_dir)
    state_path = state_file_for_vm(vm_name)
    backup_path = state_path.with_suffix(".tfstate.backup")

    provision_mode = vars.get("provision_mode") or "clone"
    # CLI uses golden/iso; Terraform expects clone/iso
    if provision_mode == "golden":
        provision_mode = "clone"

    tf_payload = {
        "vsphere_user": effective_user or "",
        "vsphere_password": effective_password or "",
        "vsphere_server": effective_server or config.vsphere_server,
        "vsphere_allow_unverified_ssl": True,
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

    cmd = [
        "terraform",
        "apply",
        "-auto-approve",
        "-input=false",
        f"-state={state_path}",
        f"-state-out={state_path}",
        f"-backup={backup_path}",
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
            f"[yellow]Note: leftover shared state at {legacy} is ignored for new builds.[/yellow]"
        )

    try:
        subprocess.run(cmd, cwd=tf_dir, env=env, check=True)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Terraform exited with code {e.returncode}[/red]")
        return False
