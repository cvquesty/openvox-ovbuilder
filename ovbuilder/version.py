"""
Version resolution for ovbuilder.

Priority (first hit wins)
-------------------------
1. Environment: OVBUILDER_VERSION or OPENVOX_BUILDER_VERSION
2. Source tree VERSION file next to the package (dev checkouts / installs
   that ship the file)
3. Package metadata ``ovbuilder.__version__`` baked at build time

This matches the ovox CLI pattern so operators can pin a displayed version
in CI without rebuilding the wheel.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import __version__ as _pkg_version


def get_version() -> str:
    """Return the best available ovbuilder version string."""
    # 1. Explicit environment override (CI / packaging wrappers).
    for env_name in ("OVBUILDER_VERSION", "OPENVOX_BUILDER_VERSION"):
        if ver := os.environ.get(env_name):
            return ver.strip()

    # 2. Development / install tree VERSION file (repo root or package dir).
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

    # 3. Baked package version from __init__.py / pyproject.
    return _pkg_version


# Eager constant for ``from ovbuilder.version import VERSION``.
VERSION = get_version()
