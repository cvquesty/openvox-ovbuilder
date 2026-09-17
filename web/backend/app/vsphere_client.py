"""Connect to vCenter using web settings, falling back to ovbuilder config."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator, Optional, Tuple

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


def _credentials(settings: Optional[Settings] = None) -> Tuple[str, str, str, str, bool]:
    settings = settings or get_settings()
    server = settings.vsphere_server
    user = settings.vsphere_user
    password = settings.vsphere_password or os.environ.get(
        "OVBUILDER_VSPHERE_PASSWORD", ""
    )
    datacenter = settings.vsphere_datacenter
    ignore_ssl = settings.vsphere_ignore_ssl

    # Admin UI runtime settings override env when present.
    try:
        from .runtime_settings import load_runtime_settings

        rt = load_runtime_settings()
        server = rt.vsphere_server or server
        user = rt.vsphere_user or user
        password = rt.vsphere_password or password
        datacenter = rt.vsphere_datacenter or datacenter
        if rt.vsphere_server or rt.vsphere_user or rt.vsphere_password:
            ignore_ssl = rt.vsphere_ignore_ssl
    except Exception:
        logger.debug("runtime settings unavailable for vSphere", exc_info=True)

    # Fall back to the CLI config when the web .env omitted a field.
    try:
        from ovbuilder.config import get_config_manager

        cfg = get_config_manager().load_config()
        server = server or cfg.vsphere_server
        datacenter = datacenter or cfg.datacenter
        ignore_ssl = cfg.vsphere_allow_unverified_ssl if ignore_ssl is None else ignore_ssl
    except Exception:
        logger.debug("ovbuilder config not available for vSphere fallback", exc_info=True)

    return server, user, password, datacenter, bool(ignore_ssl)


@contextmanager
def vsphere_session(settings: Optional[Settings] = None) -> Iterator[Tuple[object, str]]:
    """Yield (service_instance, datacenter_name). Disconnects on exit."""
    from ovbuilder import vsphere

    server, user, password, datacenter, ignore_ssl = _credentials(settings)
    if not server or not user or not password:
        raise RuntimeError(
            "vSphere credentials are not configured. "
            "Set VSPHERE_SERVER, VSPHERE_USER, and VSPHERE_PASSWORD."
        )
    si = vsphere.connect(server, user, password, ignore_ssl=ignore_ssl)
    try:
        yield si, datacenter
    finally:
        vsphere.disconnect(si)
