"""
Network helpers for ovbuilder interactive and flag-driven flows.

CIDR prefix parsing accepts the forms operators actually type:
  - bare length:     19, 24
  - slash form:      /19, /24
  - full CIDR:       10.0.0.0/19, 10.0.42.10/24
  - dotted mask:     255.255.224.0, 255.255.255.0

Any IPv4 prefix length 0–32 is allowed — segment size is not restricted
to a short “common values” list.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Union


class PrefixParseError(ValueError):
    """Raised when a user-supplied CIDR/prefix/mask cannot be interpreted."""


def prefix_to_netmask(prefix: int) -> str:
    """Return dotted-decimal IPv4 netmask for a prefix length (e.g. 19 → 255.255.224.0)."""
    if not isinstance(prefix, int) or not 0 <= prefix <= 32:
        raise PrefixParseError(f"prefix must be an integer 0–32, got {prefix!r}")
    return str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)


def parse_cidr_prefix(raw: Union[str, int, None]) -> int:
    """
    Parse a CIDR prefix length from free-form operator input.

    Returns an int in 0..32 inclusive.
    Raises PrefixParseError on empty or invalid input.
    """
    if raw is None:
        raise PrefixParseError("prefix is empty")

    if isinstance(raw, bool):
        # bool is a subclass of int — reject explicitly
        raise PrefixParseError(f"invalid CIDR prefix: {raw!r}")

    if isinstance(raw, int):
        if 0 <= raw <= 32:
            return raw
        raise PrefixParseError(f"prefix must be 0–32, got {raw}")

    s = str(raw).strip()
    if not s:
        raise PrefixParseError("prefix is empty")

    # Dotted-decimal netmask (four decimal octets)
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", s):
        try:
            net = ipaddress.IPv4Network(f"0.0.0.0/{s}")
        except (ValueError, ipaddress.NetmaskValueError) as exc:
            raise PrefixParseError(
                f"invalid IPv4 netmask {s!r} (must be contiguous, e.g. 255.255.224.0)"
            ) from exc
        return int(net.prefixlen)

    # Full CIDR or slash form: take the part after the last '/'
    #   /19  → 19
    #   10.0.0.0/19 → 19
    #   10.0.42.10/24 → 24
    if "/" in s:
        s = s.rsplit("/", 1)[-1].strip()

    # Leading slash without a prior rsplit edge case (already handled)
    if s.startswith("/"):
        s = s[1:].strip()

    if not s or not re.fullmatch(r"\d{1,2}", s):
        raise PrefixParseError(
            f"invalid CIDR prefix {raw!r}; use 0–32, /N, network/N, or a dotted mask"
        )

    prefix = int(s)
    if not 0 <= prefix <= 32:
        raise PrefixParseError(f"prefix must be 0–32, got {prefix}")
    return prefix
