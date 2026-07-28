"""Unit tests for clone-time DNF group provisioning."""

from __future__ import annotations

from ovbuilder.packages import (
    DEFAULT_DNF_GROUPS,
    build_dnf_groupinstall_script,
    sanitize_dnf_groups,
)


def test_default_groups_include_requested_set():
    for name in (
        "Server",
        "Virtualization Host",
        "Development Tools",
        "Legacy UNIX Compatibility",
        "System Tools",
    ):
        assert name in DEFAULT_DNF_GROUPS


def test_sanitize_drops_unsafe_and_dupes():
    out = sanitize_dnf_groups(
        ["Development Tools", "Development Tools", "evil;rm -rf /", "", "Server"]
    )
    assert out == ["Development Tools", "Server"]


def test_script_skips_when_empty():
    s = build_dnf_groupinstall_script([])
    assert "no groups configured" in s
    assert "groupinstall" not in s


def test_script_contains_quoted_groups():
    s = build_dnf_groupinstall_script(["Development Tools", "System Tools"])
    assert "groupinstall" in s
    assert "'Development Tools'" in s or '"Development Tools"' in s
    assert "System Tools" in s
    assert "command -v dnf" in s
