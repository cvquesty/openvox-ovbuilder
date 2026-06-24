"""
Terraform driver for ovbuilder.

Runs the existing itsys/ Terraform module (or any compatible module)
with the correct variables and environment for vSphere auth.
"""

import os
import subprocess
from pathlib import Path
from typing import Dict, Optional

from rich.console import Console

from .config import OvbuilderConfig

console = Console()


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
    """
    if not (tf_dir / "main.tf").exists():
        console.print(f"[red]No Terraform root module found at {tf_dir}[/red]")
        return False

    env = os.environ.copy()

    # Pass non-sensitive config. Prefer values from vars (discovery) over cfg defaults.
    env["TF_VAR_vsphere_datacenter"] = vars.get("datacenter", config.datacenter)
    env["TF_VAR_vsphere_cluster"] = vars.get("cluster", config.cluster)
    env["TF_VAR_vm_datastore"] = vars.get("vm_datastore", config.vm_datastore)
    env["TF_VAR_iso_datastore"] = vars.get("iso_datastore", config.iso_datastore)
    env["TF_VAR_domain"] = config.domain
    env["TF_VAR_folder"] = config.folder
    env["TF_VAR_firmware"] = config.firmware

    cmd = ["terraform", "apply", "-auto-approve"]

    # Always carry auth credentials forward explicitly.
    # Prefer values that were passed in (from interactive prompts or flags).
    # Also pull from the vars dict (defensive carry in build.py).
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

    networks_hcl = ",".join(f'"{n}"' for n in vars.get("networks", config.networks))
    cmd += [f"-var=networks=[{networks_hcl}]"]

    # guest_id can be added later; the module has a default

    console.print(f"[cyan]Running Terraform in {tf_dir}[/cyan]")

    try:
        subprocess.run(cmd, cwd=tf_dir, env=env, check=True)
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Terraform exited with code {e.returncode}[/red]")
        return False
