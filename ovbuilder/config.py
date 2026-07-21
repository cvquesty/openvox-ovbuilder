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
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "ovbuilder"
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "ovbuilder"


class GoldenImage(BaseModel):
    """One Packer-built vSphere template available in the OS picker."""

    template: str
    guest_id: str = ""
    default_user: str = "root"
    description: str = ""


def _default_golden_images() -> Dict[str, GoldenImage]:
    return {
        "almalinux-10": GoldenImage(
            template="ovbuilder-almalinux-10",
            guest_id="other4xLinux64Guest",
            default_user="almalinux",
            description="AlmaLinux 10 (Packer golden)",
        ),
        "ubuntu-24.04": GoldenImage(
            template="ovbuilder-ubuntu-24.04",
            guest_id="ubuntu64Guest",
            default_user="ubuntu",
            description="Ubuntu 24.04 LTS (Packer golden)",
        ),
    }


class OvbuilderConfig(BaseModel):
    """Persisted configuration for ovbuilder."""

    # Terraform
    terraform_dir: str = Field(
        default="",
        description="Path to the Terraform root module (defaults to bundled ./terraform)",
    )
    vsphere_server: str = "vcenter.example.com"
    vm_datastore: str = "vsanDatastore"
    iso_datastore: str = "isos"
    networks: List[str] = ["VM Production"]
    datacenter: str = "Main DC"
    cluster: str = "Production Cluster"
    domain: str = "example.com"
    folder: str = ""
    firmware: str = "efi"

    # OpenVox registration target (post-provision optional step)
    openvox_server: str = "openvox.example.com"

    # Defaults for interactive builds
    default_cpus: int = 2
    default_memory_gb: int = 4
    default_disk_gb: int = 80

    # golden = Packer templates (default); iso = legacy empty disk + ISO
    provision_mode: str = "golden"

    # Packer goldens (key shown in OS picker)
    golden_images: Dict[str, GoldenImage] = Field(default_factory=_default_golden_images)

    # Known ISOs (legacy iso mode only)
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
                    valid = {k: v for k, v in raw.items() if k in cfg.model_fields}
                    # Nested golden_images dict → GoldenImage models
                    if "golden_images" in valid and isinstance(valid["golden_images"], dict):
                        gi = {}
                        for key, val in valid["golden_images"].items():
                            if isinstance(val, dict):
                                gi[key] = GoldenImage(**val)
                            elif isinstance(val, GoldenImage):
                                gi[key] = val
                        valid["golden_images"] = gi
                    cfg = OvbuilderConfig(**{**cfg.model_dump(), **valid})
            except Exception:
                pass

        if td := os.environ.get("OVBUILDER_TERRAFORM_DIR"):
            cfg.terraform_dir = td
        if ds := os.environ.get("OVBUILDER_VM_DATASTORE"):
            cfg.vm_datastore = ds
        if ids := os.environ.get("OVBUILDER_ISO_DATASTORE"):
            cfg.iso_datastore = ids
        if srv := os.environ.get("OVBUILDER_OPENVox_SERVER"):
            cfg.openvox_server = srv
        if mode := os.environ.get("OVBUILDER_PROVISION_MODE"):
            cfg.provision_mode = mode.strip().lower()

        return cfg

    def save_config(self, cfg: OvbuilderConfig) -> None:
        self._ensure_dirs()
        data = cfg.model_dump(exclude_unset=True)
        self.config_file.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

    def get_effective_terraform_dir(self) -> Path:
        cfg = self.load_config()
        if cfg.terraform_dir:
            return Path(cfg.terraform_dir).expanduser().resolve()

        for base in ("/opt/ovbuilder", Path.home() / ".local/share/ovbuilder"):
            candidate = Path(base) / "terraform"
            if candidate.exists() and (candidate / "main.tf").exists():
                return candidate

        here = Path(__file__).resolve().parent.parent
        bundled = here / "terraform"
        if bundled.exists() and (bundled / "main.tf").exists():
            return bundled

        candidate = here.parent / "itsys"
        if candidate.exists() and (candidate / "main.tf").exists():
            return candidate

        return Path.cwd() / "terraform"


def get_config_manager() -> ConfigManager:
    return ConfigManager()
