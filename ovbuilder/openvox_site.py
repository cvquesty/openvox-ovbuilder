"""
Location-aware OpenVox endpoints and no_proxy construction.

Each site talks to its local compiler VIP. The CA is global.
GUI package repo is the local console (or corp VIP).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence
from urllib.parse import urlparse


@dataclass(frozen=True)
class OpenVoxSite:
    """One data-center's compile + GUI endpoints."""

    compiler: str
    gui: str
    ca_server: str = "ovca.corp.int-x.ai"


DEFAULT_SITES = {
    "ATLC": OpenVoxSite(
        compiler="ovcompilers.atlc-it.corp.int-x.ai",
        gui="openvox.atlc-it.corp.int-x.ai",
    ),
    "PDXC": OpenVoxSite(
        compiler="ovcompilers.pdxc-it.corp.int-x.ai",
        gui="openvox.pdxc-it.corp.int-x.ai",
    ),
}

# Always bypass Squid for these (plus per-clone FQDN/IP).
ESTATE_NO_PROXY = [
    "localhost",
    "127.0.0.1",
    "::1",
    ".corp.int-x.ai",
    "ovca.corp.int-x.ai",
    "ovcompilers.atlc-it.corp.int-x.ai",
    "ovcompilers.pdxc-it.corp.int-x.ai",
    "openvox.atlc-it.corp.int-x.ai",
    "openvox.pdxc-it.corp.int-x.ai",
    "openvox.corp.int-x.ai",
]


def infer_location(
    location: Optional[str] = None,
    hostname: str = "",
    domain: str = "",
    networks: Optional[Sequence[str]] = None,
) -> str:
    """
    Return a site code (ATLC, PDXC, …).

    Explicit ``location`` wins. Otherwise look for a site token in
    hostname, domain, and vSphere network names.
    """
    if location and str(location).strip():
        return str(location).strip().upper()
    blob = " ".join(
        [hostname or "", domain or "", " ".join(networks or [])]
    ).lower()
    for code in DEFAULT_SITES:
        if code.lower() in blob:
            return code
    return ""


def site_for(location: str, sites: Optional[dict] = None) -> OpenVoxSite:
    """Look up endpoints for a location code. Raises KeyError if unknown."""
    table = sites if sites is not None else DEFAULT_SITES
    code = (location or "").strip().upper()
    if code not in table:
        raise KeyError(
            f"Unknown OpenVox location {location!r}. "
            f"Configured: {', '.join(sorted(table))}"
        )
    return table[code]


def no_proxy_csv(
    extra: Optional[Iterable[str]] = None,
    site: Optional[OpenVoxSite] = None,
) -> str:
    """Comma-separated no_proxy list (estate + site + clone extras)."""
    items: List[str] = list(ESTATE_NO_PROXY)
    if site:
        items.extend([site.compiler, site.gui, site.ca_server])
    if extra:
        items.extend(x for x in extra if x)
    seen = set()
    out: List[str] = []
    for item in items:
        token = str(item).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return ",".join(out)


def parse_proxy_url(url: str) -> dict:
    """
    Split an HTTP proxy URL into host, port, user, password.

    Returns empty dict if url is empty. Does not log the password.
    """
    raw = (url or "").strip()
    if not raw:
        return {}
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    return {
        "url": raw if "://" in raw else f"http://{raw}",
        "scheme": parsed.scheme or "http",
        "host": parsed.hostname or "",
        "port": parsed.port,
        "username": parsed.username or "",
        "password": parsed.password or "",
    }


def agent_install_command(site: OpenVoxSite) -> str:
    """Official GUI install.bash with clustered --server / --ca-server."""
    noproxy = ",".join([site.gui, site.compiler, site.ca_server])
    repo = f"https://{site.gui}:4567/packages"
    return (
        f"curl -k --noproxy {noproxy} "
        f"{repo}/install.bash | sudo bash -s -- "
        f"--server {site.compiler} "
        f"--ca-server {site.ca_server} "
        f"--pkg-repo-url {repo}"
    )
