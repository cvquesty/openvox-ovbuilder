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

    # Pass non-sensitive config
    env["TF_VAR_vsphere_datacenter"] = config.datacenter
    env["TF_VAR_vsphere_cluster"] = config.cluster
    env["TF_VAR_vm_datastore"] = config.vm_datastore
    env["TF_VAR_iso_datastore"] = config.iso_datastore
    env["TF_VAR_domain"] = config.domain
    env["TF_VAR_folder"] = config.folder
    env["TF_VAR_firmware"] = config.firmware

    if vsphere_user:
        env["TF_VAR_vsphere_user"] = vsphere_user
    if vsphere_password:
        env["TF_VAR_vsphere_password"] = vsphere_password

    cmd = ["terraform", "apply", "-auto-approve"]

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
