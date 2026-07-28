"""
Clone-time package / DNF group provisioning.

=============================================================================
WHY
=============================================================================
Packer goldens (and manual ISO "Minimal Install") ship a lean ``@core``
image. Operators still want a standard set of EL environment/package groups
on every new VM without re-running Anaconda.

These groups are applied at **clone / post-install time** (not in kickstart):

  * Golden path — cloud-init ``runcmd`` after network identity is applied.
  * ISO / optional SSH path — Paramiko step before OpenVox agent bootstrap.

Ubuntu/Debian guests skip DNF groups (no ``dnf``/``yum``).

=============================================================================
CONFIG
=============================================================================
``OvbuilderConfig.dnf_groups`` — list of comps *display names* (same labels
as Anaconda Software Selection). Empty list disables the step.
"""

from __future__ import annotations

import re
import shlex
from typing import Iterable, List, Sequence

# Default EL groups requested for OpenVox lab / fleet nodes.
# Names match ``dnf group list`` / Anaconda display names.
DEFAULT_DNF_GROUPS: List[str] = [
    "Server",
    "Virtualization Host",
    "Console Internet Tools",
    "Container Management",
    "RPM Development Tools",
    "Development Tools",
    "Headless Management",
    "Legacy UNIX Compatibility",
    "Network Servers",
    "Scientific Support",
    "Security Tools",
    "System Tools",
]

# Allow letters, digits, spaces, and common comps punctuation.
_SAFE_GROUP = re.compile(r"^[A-Za-z0-9 ._+/-]+$")


def sanitize_dnf_groups(groups: Iterable[str] | None) -> List[str]:
    """
    Return a de-duplicated list of shell-safe group display names.

    Invalid / empty names are dropped (not raised) so a typo in config
    does not abort the whole clone.
    """
    out: List[str] = []
    seen = set()
    for raw in groups or []:
        name = (raw or "").strip()
        if not name or name in seen:
            continue
        if not _SAFE_GROUP.match(name):
            continue
        seen.add(name)
        out.append(name)
    return out


def build_dnf_groupinstall_script(groups: Sequence[str]) -> str:
    """
    Bash script (with shebang) that installs DNF/YUM groups if present.

    Group failures are warnings — the script exits 0 so cloud-init / SSH
    provision continues (network + agent still matter more than one comps
    group that may be renamed on a given Alma minor).
    """
    clean = sanitize_dnf_groups(groups)
    if not clean:
        return (
            "#!/bin/bash\n"
            "echo 'ovbuilder-dnf: no groups configured — skipping'\n"
            "exit 0\n"
        )

    # Quoted array literal for bash.
    quoted = " ".join(shlex.quote(g) for g in clean)
    return f"""#!/bin/bash
# ovbuilder clone-time DNF group install (EL only).
set +e
echo "ovbuilder-dnf: starting group install"

if ! command -v dnf >/dev/null 2>&1 && ! command -v yum >/dev/null 2>&1; then
  echo "ovbuilder-dnf: no dnf/yum on PATH — skipping (Debian/Ubuntu or minimal)"
  exit 0
fi

PKG=$(command -v dnf 2>/dev/null || command -v yum)
echo "ovbuilder-dnf: using $PKG"
$PKG -y makecache 2>/dev/null || true

GROUPS=({quoted})
failed=0
for g in "${{GROUPS[@]}}"; do
  echo "ovbuilder-dnf: groupinstall '$g'"
  if $PKG -y groupinstall "$g" || $PKG -y group install "$g"; then
    echo "ovbuilder-dnf: ok '$g'"
  else
    echo "ovbuilder-dnf: WARNING failed to install group '$g'" >&2
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "ovbuilder-dnf: finished with some group failures (non-fatal)"
else
  echo "ovbuilder-dnf: all requested groups installed"
fi
exit 0
"""
