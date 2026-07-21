"""
Terraform driver for ovbuilder.

Runs the bundled (or configured) Terraform root module with the correct
variables and environment for vSphere auth.

CRITICAL: each VM gets its own local Terraform state file under
  $XDG_DATA_HOME/ovbuilder/tfstate/<vm_name>/terraform.tfstate
so that building ovca3 never renames/updates ovca2. A single shared
state file was the previous bug (Terraform treated vm_name as an
in-place update of module.vm).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, Optional

from rich.console import Console

from .config import ConfigManager, OvbuilderConfig, get_config_manager

console = Console()

# Terraform resource address is always module.vm — isolation is via state path.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_state_key(vm_name: str) -> str:
    """Filesystem-safe key for per-VM state directories."""
    key = (vm_name or "unnamed").strip()
    key = _SAFE_NAME.sub("_", key)
    key = key.strip("._-") or "unnamed"
    return key[:128]


def state_dir_for_vm(vm_name: str, data_dir: Optional[Path] = None) -> Path:
    """Return ~/.local/share/ovbuilder/tfstate/<vm>/ (or XDG_DATA_HOME)."""
    if data_dir is None:
        data_dir = get_config_manager().data_dir
    path = Path(data_dir) / "tfstate" / sanitize_state_key(vm_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_file_for_vm(vm_name: str, data_dir: Optional[Path] = None) -> Path:
    return state_dir_for_vm(vm_name, data_dir=data_dir) / "terraform.tfstate"


def _vm_name_from_state_file(state_path: Path) -> Optional[str]:
    """Best-effort read of vsphere_virtual_machine name from a local state file."""
    try:
        import json

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
    Move the old single terraform.tfstate in the module directory into the
    per-VM path once, so existing inventory is not abandoned.
    """
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
            f"[yellow]Legacy state at {legacy} has no VM name; leave it in place "
            "and inspect manually.[/yellow]"
        )
        return

    dest = state_file_for_vm(vm_name)
    if dest.exists():
        # Per-VM state already present — rename legacy aside so it is not reused
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
            f"[dim]Tracked VM: {vm_name}. Future builds of other hostnames "
            "will use separate state files and will not rename this VM.[/dim]"
        )
    except OSError as exc:
        console.print(f"[yellow]Could not migrate legacy state: {exc}[/yellow]")


def ensure_terraform_init(tf_dir: Path, env: dict) -> bool:
    """Run terraform init -input=false if providers are not yet installed."""
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


def run_terraform_apply(
    tf_dir: Path,
    vars: Dict,
    config: OvbuilderConfig,
    vsphere_user: Optional[str] = None,
    vsphere_password: Optional[str] = None,
) -> bool:
    """
    Execute terraform apply against the given module.

    Sensitive values are passed via TF_VAR_* environment variables.
    State is isolated per vm_name so each build creates a new VM.
    """
    if not (tf_dir / "main.tf").exists():
        console.print(f"[red]No Terraform root module found at {tf_dir}[/red]")
        return False

    vm_name = vars.get("vm_name") or "unnamed"
    state_path = state_file_for_vm(vm_name)
    backup_path = state_path.with_suffix(".tfstate.backup")

    env = os.environ.copy()

    # Pass non-sensitive config. Prefer values from vars (discovery) over cfg defaults.
    env["TF_VAR_vsphere_datacenter"] = vars.get("datacenter", config.datacenter)
    env["TF_VAR_vsphere_cluster"] = vars.get("cluster", config.cluster)
    env["TF_VAR_vm_datastore"] = vars.get("vm_datastore", config.vm_datastore)
    env["TF_VAR_iso_datastore"] = vars.get("iso_datastore", config.iso_datastore)
    env["TF_VAR_domain"] = config.domain
    env["TF_VAR_folder"] = config.folder
    env["TF_VAR_firmware"] = config.firmware

    if not ensure_terraform_init(tf_dir, env):
        return False

    # One-time: move old shared terraform.tfstate out of the module dir
    migrate_legacy_shared_state(tf_dir)
    # Re-resolve path in case migration just created it for this vm_name
    state_path = state_file_for_vm(vm_name)
    backup_path = state_path.with_suffix(".tfstate.backup")

    cmd = [
        "terraform",
        "apply",
        "-auto-approve",
        "-input=false",
        # Per-VM local state — never reuse the module dir's default terraform.tfstate
        f"-state={state_path}",
        f"-state-out={state_path}",
        f"-backup={backup_path}",
    ]

    # Always carry auth credentials forward explicitly.
    effective_user = vsphere_user or vars.get("vsphere_user")
    effective_password = vsphere_password or vars.get("vsphere_password")
    effective_server = vars.get("vsphere_server") or getattr(config, "vsphere_server", None)

    if effective_user:
        env["TF_VAR_vsphere_user"] = effective_user
        env["VSPHERE_USER"] = effective_user
        cmd += [f"-var=vsphere_user={effective_user}"]
    if effective_password:
        env["TF_VAR_vsphere_password"] = effective_password
        env["VSPHERE_PASSWORD"] = effective_password
        cmd += [f"-var=vsphere_password={effective_password}"]

    if effective_server:
        env["TF_VAR_vsphere_server"] = effective_server
        env["VSPHERE_SERVER"] = effective_server
        cmd += [f"-var=vsphere_server={effective_server}"]

    # VM specific
    cmd += [f"-var=vm_name={vars['vm_name']}"]
    cmd += [f"-var=iso_path={vars['iso_path']}"]
    cmd += [f"-var=num_cpus={vars['num_cpus']}"]
    cmd += [f"-var=memory_mb={vars['memory_mb']}"]
    cmd += [f"-var=disk_size_gb={vars['disk_size_gb']}"]

    if vars.get("datacenter"):
        cmd += [f"-var=vsphere_datacenter={vars['datacenter']}"]
    if vars.get("cluster"):
        cmd += [f"-var=vsphere_cluster={vars['cluster']}"]
    if vars.get("vm_datastore"):
        cmd += [f"-var=vm_datastore={vars['vm_datastore']}"]
    if vars.get("iso_datastore"):
        cmd += [f"-var=iso_datastore={vars['iso_datastore']}"]

    networks_hcl = ",".join(f'"{n}"' for n in vars.get("networks", config.networks))
    cmd += [f"-var=networks=[{networks_hcl}]"]

    console.print(f"[cyan]Running Terraform in {tf_dir}[/cyan]")
    console.print(f"[dim]State (isolated per VM): {state_path}[/dim]")
    if state_path.exists():
        console.print(
            "[dim]Existing state for this hostname — apply will update "
            f"[bold]{vm_name}[/bold] only, not other VMs.[/dim]"
        )
    else:
        console.print(
            f"[dim]New state for [bold]{vm_name}[/bold] — a new VM will be created.[/dim]"
        )

    # Warn if legacy shared state still exists in the module dir (old installs)
    legacy = tf_dir / "terraform.tfstate"
    if legacy.exists():
        console.print(
            "[yellow]Note: legacy shared state still exists at "
            f"{legacy}. New builds no longer use it. See CHANGELOG / README "
            "for recovery if a previous apply renamed a VM.[/yellow]"
        )

    try:
        subprocess.run(cmd, cwd=tf_dir, env=env, check=True)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Terraform exited with code {e.returncode}[/red]")
        return False


def run_terraform_destroy(
    tf_dir: Path,
    vm_name: str,
    config: OvbuilderConfig,
    vsphere_user: Optional[str] = None,
    vsphere_password: Optional[str] = None,
    vsphere_server: Optional[str] = None,
) -> bool:
    """Destroy a single VM using its isolated state file (if present)."""
    state_path = state_file_for_vm(vm_name)
    if not state_path.exists():
        console.print(
            f"[red]No per-VM state for {vm_name!r} at {state_path}[/red]\n"
            "[dim]Cannot destroy via Terraform without that state.[/dim]"
        )
        return False

    env = os.environ.copy()
    if vsphere_user:
        env["TF_VAR_vsphere_user"] = vsphere_user
        env["VSPHERE_USER"] = vsphere_user
    if vsphere_password:
        env["TF_VAR_vsphere_password"] = vsphere_password
        env["VSPHERE_PASSWORD"] = vsphere_password
    if vsphere_server or config.vsphere_server:
        srv = vsphere_server or config.vsphere_server
        env["TF_VAR_vsphere_server"] = srv
        env["VSPHERE_SERVER"] = srv

    cmd = [
        "terraform",
        "destroy",
        "-auto-approve",
        "-input=false",
        f"-state={state_path}",
        f"-state-out={state_path}",
        f"-var=vm_name={vm_name}",
    ]
    # Minimal required vars — destroy still needs provider + some vars present
    cmd += [f"-var=iso_path=unused"]
    cmd += [f"-var=vsphere_datacenter={config.datacenter}"]
    cmd += [f"-var=vsphere_cluster={config.cluster}"]
    cmd += [f"-var=vm_datastore={config.vm_datastore}"]

    console.print(f"[cyan]Destroying {vm_name} using state {state_path}[/cyan]")
    try:
        subprocess.run(cmd, cwd=tf_dir, env=env, check=True)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]terraform destroy failed (exit {e.returncode})[/red]")
        return False
