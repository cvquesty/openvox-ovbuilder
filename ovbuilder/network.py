"""
IPv4 CIDR / netmask parsing for ovbuilder interview and CLI flags.

=============================================================================
WHY
=============================================================================
Operators type prefixes many ways: ``19``, ``/19``, ``10.0.0.0/19``,
``255.255.224.0``. Early builds called ``int(raw)`` and crashed on ``/19``.
This module is the single source of truth for turning free-form input into
a validated prefix length 0–32 (any segment size — no “common only” list).

Used by:
  * interactive Prompt in build.py
  * ``--prefix`` / ``--cidr`` flags
  * cloud_init guestinfo address strings (``ip/prefix``)
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable, List, Optional, Sequence, Union


class PrefixParseError(ValueError):
    """
    Raised when user input cannot be turned into a valid IPv4 prefix.

    Subclasses ValueError so generic ``except ValueError`` still works,
    but callers should catch PrefixParseError for re-prompt UX.
    """


def prefix_to_netmask(prefix: int) -> str:
    """
    Convert a prefix length to dotted-decimal netmask.

    Examples
    --------
    >>> prefix_to_netmask(24)
    '255.255.255.0'
    >>> prefix_to_netmask(19)
    '255.255.224.0'

    Implementation uses the stdlib IPv4Network with host bits zeroed so we
    never hand-roll bit shifts (error-prone at edge lengths 0 and 32).
    """
    if not isinstance(prefix, int) or not 0 <= prefix <= 32:
        raise PrefixParseError(f"prefix must be an integer 0–32, got {prefix!r}")
    # 0.0.0.0/<prefix> is a valid network; .netmask is the dotted mask.
    return str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)


def parse_cidr_prefix(raw: Union[str, int, None]) -> int:
    """
    Parse free-form operator input into an IPv4 prefix length (0–32).

    Accepted forms
    --------------
    * Integer: ``19``, ``24``
    * Slash form: ``/19``
    * Network/prefix: ``10.0.0.0/19``, ``10.0.42.10/24`` (prefix only is used)
    * Dotted mask: ``255.255.224.0`` (must be contiguous)

    Rejected
    --------
    * Empty / whitespace-only
    * Non-contiguous masks (e.g. 255.255.0.255)
    * Prefix outside 0–32
    * bool (because ``bool`` is a subclass of ``int`` in Python)

    Returns
    -------
    int
        Prefix length suitable for ``ip/prefix`` strings and cloud-init.
    """
    # --- None / empty -------------------------------------------------------
    if raw is None:
        raise PrefixParseError("prefix is empty")

    # bool is a subclass of int: True→1 would silently become /1. Reject.
    if isinstance(raw, bool):
        raise PrefixParseError(f"invalid CIDR prefix: {raw!r}")

    # --- Already an int -----------------------------------------------------
    if isinstance(raw, int):
        if 0 <= raw <= 32:
            return raw
        raise PrefixParseError(f"prefix must be 0–32, got {raw}")

    # --- String forms -------------------------------------------------------
    s = str(raw).strip()
    if not s:
        raise PrefixParseError("prefix is empty")

    # Dotted-decimal netmask: exactly four decimal octets.
    # Example: 255.255.224.0 → prefix 19
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", s):
        try:
            # IPv4Network validates contiguity of the mask.
            net = ipaddress.IPv4Network(f"0.0.0.0/{s}")
        except (ValueError, ipaddress.NetmaskValueError) as exc:
            raise PrefixParseError(
                f"invalid IPv4 netmask {s!r} "
                f"(must be contiguous, e.g. 255.255.224.0)"
            ) from exc
        return int(net.prefixlen)

    # Slash forms: take the segment after the last '/'.
    #   "/19"           → "19"
    #   "10.0.0.0/19"   → "19"
    #   "10.0.42.10/24" → "24"
    if "/" in s:
        s = s.rsplit("/", 1)[-1].strip()

    # Defensive: if something left a leading slash, strip it.
    if s.startswith("/"):
        s = s[1:].strip()

    # Only pure decimal digits (1–2 chars covers 0–32).
    if not s or not re.fullmatch(r"\d{1,2}", s):
        raise PrefixParseError(
            f"invalid CIDR prefix {raw!r}; "
            f"use 0–32, /N, network/N, or a dotted mask"
        )

    prefix = int(s)
    if not 0 <= prefix <= 32:
        raise PrefixParseError(f"prefix must be 0–32, got {prefix}")
    return prefix


# Hostnames / IPs safe to embed in guest shell + netplan / nmcli.
_DNS_TOKEN = re.compile(
    r"^(?:"
    r"(?:\d{1,3}\.){3}\d{1,3}"  # IPv4
    r"|(?:[A-Fa-f0-9:]+)"  # rough IPv6
    r"|(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63})*)"
    r")$"
)


def is_plausible_dns(value: str) -> bool:
    """True if value looks like an IPv4/IPv6 address or DNS hostname."""
    s = (value or "").strip()
    if not s or not _DNS_TOKEN.match(s):
        return False
    # Prefer stdlib for IPv4/IPv6; hostnames already matched above.
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return "." in s or s.isalnum() or "-" in s


def normalize_dns_servers(
    dns: Optional[Union[str, Sequence[str]]] = None,
) -> List[str]:
    """
    Normalize operator DNS input to an ordered, de-duplicated list.

    Accepts ``None``, a single string (optionally comma/space separated),
    or a sequence of strings (each may itself be comma-separated).
    """
    if dns is None:
        return []
    chunks: Iterable[str]
    if isinstance(dns, str):
        chunks = [dns]
    else:
        chunks = dns

    out: List[str] = []
    seen = set()
    for chunk in chunks:
        if chunk is None:
            continue
        for part in re.split(r"[\s,;]+", str(chunk).strip()):
            token = part.strip()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
    return out
