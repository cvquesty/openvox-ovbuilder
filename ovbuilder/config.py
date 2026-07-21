"""
XDG-based configuration for ovbuilder (pydantic model + YAML load/save).

=============================================================================
LOCATIONS (same pattern as ovox CLI)
=============================================================================
  Config : $XDG_CONFIG_HOME/ovbuilder/config.yaml
           default ~/.config/ovbuilder/config.yaml
  Data   : $XDG_DATA_HOME/ovbuilder/
           default ~/.local/share/ovbuilder/
           (per-VM Terraform state lives under data/tfstate/<vm>/)

Environment overrides (selected keys):
  OVBUILDER_TERRAFORM_DIR
  OVBUILDER_VM_DATASTORE
  OVBUILDER_ISO_DATASTORE
  OVBUILDER_OPENVox_SERVER
  OVBUILDER_PROVISION_MODE   # golden | iso

=============================================================================
PROVISION MODES
=============================================================================
  golden (default) — clone Packer template; inject cloud-init guestinfo
  iso              — empty disk + attach install ISO (legacy)

Credentials are intentionally NOT stored in config.yaml by default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


class GoldenImage(BaseModel):
    """
    One Packer-built vSphere template offered in the OS picker.

    Attributes
    ----------
    template : inventory name of the Template / VM template in vCenter
    guest_id : vSphere guestId (empty = inherit from template at clone)
    default_user : SSH user baked into the golden (almalinux / ubuntu)
    description : human label for the interactive table
    """

    template: str
    guest_id: str = ""
    default_user: str = "root"
    description: str = ""


def _default_golden_images() -> Dict[str, GoldenImage]:
    """
    Factory for default goldens shipped with ovbuilder.

    Keys are stable CLI ``--os`` identifiers. Template names must match
    what Packer produces (see packer/*/ *.pkr.hcl ``template_name``).
    """
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
    """
    Full persisted configuration model.

    Defaults are lab-oriented placeholders; real sites override via YAML
    or environment variables after first install.
    """

    # --- Terraform module location ------------------------------------------
    terraform_dir: str = Field(
        default="",
        description="Path to Terraform root (empty = discover bundled/install path)",
    )

    # --- vSphere placement defaults (overridden by interactive discovery) ---
    vsphere_server: str = "vcenter.example.com"
    vm_datastore: str = "vsanDatastore"
    iso_datastore: str = "isos"
    networks: List[str] = Field(default_factory=lambda: ["VM Production"])
    datacenter: str = "Main DC"
    cluster: str = "Production Cluster"
    domain: str = "example.com"
    folder: str = ""
    firmware: str = "efi"
    # Allow self-signed vCenter certs in Terraform provider (lab default).
    vsphere_allow_unverified_ssl: bool = True

    # --- OpenVox -------------------------------------------------------------
    openvox_server: str = "openvox.example.com"

    # --- Interactive sizing defaults ----------------------------------------
    default_cpus: int = 2
    default_memory_gb: int = 4
    default_disk_gb: int = 80

    # --- Mode + goldens -----------------------------------------------------
    provision_mode: str = "golden"  # or "iso"
    golden_images: Dict[str, GoldenImage] = Field(
        default_factory=_default_golden_images
    )

    # --- Legacy ISO shortcuts (iso mode only) -------------------------------
    known_isos: Dict[str, str] = Field(
        default_factory=lambda: {
            "AlmaLinux 9.4": "isos/AlmaLinux-9.4-x86_64-dvd.iso",
            "Rocky Linux 9.4": "isos/Rocky-9.4-x86_64-dvd.iso",
            "RHEL 9.4": "isos/rhel-9.4-x86_64-dvd.iso",
            "Ubuntu 24.04": "isos/ubuntu-24.04-live-server-amd64.iso",
        }
    )


class ConfigManager:
    """
    Load/save OvbuilderConfig and resolve the effective Terraform directory.

    Directory permissions: config dir is chmod 0700 when possible so that
    future optional secret fields are not world-readable.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        # Resolve XDG paths once; allow test injection via config_dir.
        self.config_dir = config_dir or (
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "ovbuilder"
        )
        self.data_dir = (
            Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
            / "ovbuilder"
        )
        self.config_file = self.config_dir / "config.yaml"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create config/data directories; best-effort private mode on config."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.config_dir.chmod(0o700)
        except OSError:
            pass

    def load_config(self) -> OvbuilderConfig:
        """
        Merge defaults ← YAML file ← environment overrides.

        YAML load failures are swallowed (corrupt file → defaults) so the
        CLI still starts; operators can re-save a clean config later.
        """
        cfg = OvbuilderConfig()

        if self.config_file.exists():
            try:
                raw = yaml.safe_load(self.config_file.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    # Only known model fields — ignore typos/extra keys quietly.
                    valid = {k: v for k, v in raw.items() if k in cfg.model_fields}
                    # Nested golden_images: dict → GoldenImage instances
                    if "golden_images" in valid and isinstance(
                        valid["golden_images"], dict
                    ):
                        gi: Dict[str, GoldenImage] = {}
                        for key, val in valid["golden_images"].items():
                            if isinstance(val, dict):
                                gi[key] = GoldenImage(**val)
                            elif isinstance(val, GoldenImage):
                                gi[key] = val
                        valid["golden_images"] = gi
                    cfg = OvbuilderConfig(**{**cfg.model_dump(), **valid})
            except Exception:
                # Keep defaults; do not crash CLI on bad YAML.
                pass

        # Environment overrides (non-secret placement / mode knobs)
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
        """Write YAML (exclude_unset keeps file minimal)."""
        self._ensure_dirs()
        data = cfg.model_dump(exclude_unset=True)
        self.config_file.write_text(
            yaml.safe_dump(data, sort_keys=True), encoding="utf-8"
        )

    def get_effective_terraform_dir(self) -> Path:
        """
        Resolve Terraform root module path in priority order:

          1. config.terraform_dir (explicit)
          2. /opt/ovbuilder/terraform (system install)
          3. ~/.local/share/ovbuilder/terraform (user install)
          4. bundled repo ./terraform next to the package
          5. legacy ../itsys (historical monorepo layout)
          6. cwd/terraform
        """
        cfg = self.load_config()
        if cfg.terraform_dir:
            return Path(cfg.terraform_dir).expanduser().resolve()

        for base in ("/opt/ovbuilder", Path.home() / ".local/share/ovbuilder"):
            candidate = Path(base) / "terraform"
            if candidate.exists() and (candidate / "main.tf").exists():
                return candidate

        # ovbuilder/config.py → package root → terraform/
        here = Path(__file__).resolve().parent.parent
        bundled = here / "terraform"
        if bundled.exists() and (bundled / "main.tf").exists():
            return bundled

        candidate = here.parent / "itsys"
        if candidate.exists() and (candidate / "main.tf").exists():
            return candidate

        return Path.cwd() / "terraform"


def get_config_manager() -> ConfigManager:
    """Module-level factory (simple; no singleton lock needed for CLI)."""
    return ConfigManager()
