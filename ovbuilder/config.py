"""
Configuration management for ovbuilder.

Follows the same XDG + env + pydantic style as the ovox CLI.

Locations:
  $XDG_CONFIG_HOME/ovbuilder/   or ~/.config/ovbuilder/
      config.yaml
  $XDG_DATA_HOME/ovbuilder/

Environment overrides:
  OVBUILDER_TERRAFORM_DIR
  OVBUILDER_ISO_DATASTORE
  etc.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "ovbuilder"
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "ovbuilder"


class OvbuilderConfig(BaseModel):
    """Persisted configuration for ovbuilder."""

    # Terraform
    terraform_dir: str = Field(
        default="",
        description="Path to the directory containing the Terraform root module for VM provisioning (defaults to bundled ./terraform if not set)"
    )
    vm_datastore: str = "vsanDatastore"
    iso_datastore: str = "isos"
    networks: List[str] = ["VM Production"]
    datacenter: str = "SMFC DC"
    cluster: str = "SMFC Compute"
    domain: str = "smfc-it.twitter.biz"
    folder: str = ""
    firmware: str = "efi"

    # OpenVox registration target (used in post-provision step)
    openvox_server: str = "openvox.pdxc-it.twitter.biz"

    # Defaults for interactive builds
    default_cpus: int = 2
    default_memory_gb: int = 4
    default_disk_gb: int = 80

    # Known ISOs (name -> path on iso_datastore)
    known_isos: Dict[str, str] = Field(
        default_factory=lambda: {
            "AlmaLinux 9.4": "isos/AlmaLinux-9.4-x86_64-dvd.iso",
            "Rocky Linux 9.4": "isos/Rocky-9.4-x86_64-dvd.iso",
            "RHEL 9.4": "isos/rhel-9.4-x86_64-dvd.iso",
            "Ubuntu 24.04": "isos/ubuntu-24.04-live-server-amd64.iso",
        }
    )


class ConfigManager:
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        ) / "ovbuilder"
        self.data_dir = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        ) / "ovbuilder"
        self.config_file = self.config_dir / "config.yaml"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.config_dir.chmod(0o700)
        except OSError:
            pass

    def load_config(self) -> OvbuilderConfig:
        cfg = OvbuilderConfig()

        if self.config_file.exists():
            try:
                raw = yaml.safe_load(self.config_file.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    # Only take fields that exist in the model
                    valid = {k: v for k, v in raw.items() if k in cfg.model_fields}
                    cfg = OvbuilderConfig(**{**cfg.model_dump(), **valid})
            except Exception:
                pass

        # Env overrides (common ones)
        if td := os.environ.get("OVBUILDER_TERRAFORM_DIR"):
            cfg.terraform_dir = td
        if ds := os.environ.get("OVBUILDER_VM_DATASTORE"):
            cfg.vm_datastore = ds
        if ids := os.environ.get("OVBUILDER_ISO_DATASTORE"):
            cfg.iso_datastore = ids
        if srv := os.environ.get("OVBUILDER_OPENVox_SERVER"):
            cfg.openvox_server = srv

        return cfg

    def save_config(self, cfg: OvbuilderConfig) -> None:
        self._ensure_dirs()
        data = cfg.model_dump(exclude_unset=True)
        self.config_file.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

    def get_effective_terraform_dir(self) -> Path:
        cfg = self.load_config()
        if cfg.terraform_dir:
            return Path(cfg.terraform_dir).expanduser().resolve()
        # Default to the bundled terraform/ directory inside this project
        # (self-contained repo with modules/vm for ISO provisioning)
        here = Path(__file__).resolve().parent.parent  # ovbuilder/ovbuilder/ -> ovbuilder/
        bundled = here / "terraform"
        if bundled.exists():
            return bundled
        # Fallback for development in larger workspace
        candidate = here.parent / "itsys"
        if candidate.exists():
            return candidate
        return Path.cwd() / "terraform"


def get_config_manager() -> ConfigManager:
    return ConfigManager()
