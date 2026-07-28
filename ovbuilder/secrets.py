"""
Local secrets for ovbuilder — never committed, never logged.

Resolution order for the golden / clone login password
------------------------------------------------------
1. Environment: ``OVBUILDER_GOLDEN_PASSWORD``
2. Local env-file: ``<config_dir>/secrets.env`` (mode 0600 when the OS allows)
3. Local YAML: ``<config_dir>/secrets.yaml`` key ``golden_password``

``config_dir`` is platform-aware (see ``ovbuilder.paths.config_dir``).

vSphere credentials stay interactive / CLI flags / env
(``VSPHERE_PASSWORD``, ``TF_VAR_vsphere_password``) — not stored in the
repo. Packer uses gitignored ``packer/variables.auto.pkrvars.hcl``.

Nothing in this module embeds a default password string.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from .paths import config_dir, is_windows

# Public env name — operators export this in CI or shell.
ENV_GOLDEN_PASSWORD = "OVBUILDER_GOLDEN_PASSWORD"


def secrets_dir() -> Path:
    """Config dir for secrets (same tree as ConfigManager)."""
    return config_dir()


def secrets_env_path() -> Path:
    return secrets_dir() / "secrets.env"


def secrets_yaml_path() -> Path:
    return secrets_dir() / "secrets.yaml"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser (no export, no shell expansion)."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key:
            out[key] = val
    return out


def get_golden_password() -> Optional[str]:
    """
    Return the operator-supplied golden password, or None if unset.

    Callers must treat None as "do not inject passwords" or fail the build
    with a clear message — never fall back to a baked-in default.
    """
    env = os.environ.get(ENV_GOLDEN_PASSWORD, "").strip()
    if env:
        return env

    env_file = secrets_env_path()
    if env_file.is_file():
        parsed = _parse_env_file(env_file)
        for key in (ENV_GOLDEN_PASSWORD, "GOLDEN_PASSWORD", "ssh_password"):
            if parsed.get(key, "").strip():
                return parsed[key].strip()

    yml = secrets_yaml_path()
    if yml.is_file():
        try:
            raw = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            raw = {}
        if isinstance(raw, dict):
            for key in ("golden_password", "ssh_password", ENV_GOLDEN_PASSWORD):
                val = raw.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()

    return None


def golden_password_setup_help() -> str:
    """
    Operator-facing instructions when the golden password is missing.

    Includes both UNIX shell and Windows PowerShell snippets.
    """
    env_path = secrets_env_path()
    directory = secrets_dir()
    unix = (
        f"  mkdir -p {directory}\n"
        f"  chmod 700 {directory}\n"
        f"  printf \"%s\\n\" \"{ENV_GOLDEN_PASSWORD}='your-lab-password'\" > {env_path}\n"
        f"  chmod 600 {env_path}\n"
        "\n"
        "  # or this shell only:\n"
        f"  export {ENV_GOLDEN_PASSWORD}='your-lab-password'\n"
    )
    windows = (
        f"  New-Item -ItemType Directory -Force -Path \"{directory}\"\n"
        f"  Set-Content -Path \"{env_path}\" -Value \"{ENV_GOLDEN_PASSWORD}=your-lab-password\"\n"
        "\n"
        "  # or this session only:\n"
        f"  $env:{ENV_GOLDEN_PASSWORD} = 'your-lab-password'\n"
    )
    primary = windows if is_windows() else unix
    secondary_label = "UNIX / macOS / Linux / Git Bash / WSL" if is_windows() else "Windows PowerShell"
    secondary = unix if is_windows() else windows
    return (
        "Golden guest password is not configured.\n"
        "\n"
        "ovbuilder will not bake a default password into git or the install.\n"
        "Create a local secrets file (never commit it).\n"
        "\n"
        f"Resolved secrets path: {env_path}\n"
        "\n"
        "This platform:\n"
        f"{primary}\n"
        f"{secondary_label}:\n"
        f"{secondary}\n"
        "Then re-run:  ovbuilder build\n"
        "\n"
        "Details: docs/SECRETS.md"
    )


def require_golden_password(context: str = "golden clone") -> str:
    """
    Return golden password or raise RuntimeError with setup instructions.
    """
    pw = get_golden_password()
    if pw:
        return pw
    raise RuntimeError(
        f"No golden password configured for {context}.\n\n"
        + golden_password_setup_help()
    )
