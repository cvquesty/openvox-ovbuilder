"""
Version handling for ovbuilder.

Follows the same patterns as the ovox CLI.
"""

import os
from pathlib import Path

from . import __version__ as _pkg_version


def get_version() -> str:
    """Return the best available ovbuilder version string."""
    # 1. Explicit environment override
    for env_name in ("OVBUILDER_VERSION", "OPENVOX_BUILDER_VERSION"):
        if ver := os.environ.get(env_name):
            return ver.strip()

    # 2. Development / source tree VERSION file
    for candidate in (
        Path(__file__).resolve().parent.parent / "VERSION",
        Path(__file__).resolve().parent / "VERSION",
    ):
        try:
            if candidate.exists():
                ver = candidate.read_text(encoding="utf-8").strip()
                if ver:
                    return ver
        except (OSError, PermissionError):
            pass

    # 3. Baked package version
    return _pkg_version


VERSION = get_version()
