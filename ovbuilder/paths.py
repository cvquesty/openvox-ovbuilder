"""
Platform-aware config and data directories.

=============================================================================
RESOLUTION ORDER
=============================================================================
Config directory
  1. ``XDG_CONFIG_HOME/ovbuilder`` if set (any OS)
  2. Windows: ``%APPDATA%\\ovbuilder``
  3. macOS / Linux: ``~/.config/ovbuilder``

Data directory (Terraform state, etc.)
  1. ``XDG_DATA_HOME/ovbuilder`` if set
  2. Windows: ``%LOCALAPPDATA%\\ovbuilder``
  3. macOS / Linux: ``~/.local/share/ovbuilder``

Operators on Windows can still force UNIX-style trees by setting the XDG_*
variables (useful in Git Bash / WSL consistency).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    """True on native Windows (not WSL, which reports linux)."""
    return sys.platform == "win32"


def config_dir() -> Path:
    """Directory for config.yaml and secrets.env."""
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg).expanduser() / "ovbuilder"
    if is_windows():
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "ovbuilder"
    return Path.home() / ".config" / "ovbuilder"


def data_dir() -> Path:
    """Directory for per-VM Terraform state and other runtime data."""
    if xdg := os.environ.get("XDG_DATA_HOME"):
        return Path(xdg).expanduser() / "ovbuilder"
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(base) / "ovbuilder"
    return Path.home() / ".local" / "share" / "ovbuilder"
