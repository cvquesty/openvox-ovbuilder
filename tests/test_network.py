"""Unit tests for ovbuilder.network CIDR / prefix parsing."""

from __future__ import annotations

import pytest

from ovbuilder.network import PrefixParseError, parse_cidr_prefix, prefix_to_netmask


@pytest.mark.parametrize(
    "raw,expected",
    [
        (19, 19),
        ("19", 19),
        ("/19", 19),
        (" /19 ", 19),
        ("10.0.0.0/19", 19),
        ("10.0.42.10/24", 24),
        ("255.255.224.0", 19),
        ("255.255.255.0", 24),
        ("255.255.252.0", 22),
        ("255.255.255.128", 25),
        ("0", 0),
        ("32", 32),
        ("/32", 32),
        ("/0", 0),
        ("8", 8),
        ("16", 16),
        ("23", 23),
        (" 24 ", 24),
    ],
)
def test_parse_cidr_prefix_accepts_common_forms(raw, expected):
    assert parse_cidr_prefix(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        None,
        "abc",
        "/99",
        "99",
        "-1",
        "255.255.0.255",  # non-contiguous mask
        "300.0.0.0",
        "10.0.0.0/",
        "/",
        True,
    ],
)
def test_parse_cidr_prefix_rejects_invalid(raw):
    with pytest.raises(PrefixParseError):
        parse_cidr_prefix(raw)


def test_prefix_to_netmask_examples():
    assert prefix_to_netmask(19) == "255.255.224.0"
    assert prefix_to_netmask(22) == "255.255.252.0"
    assert prefix_to_netmask(24) == "255.255.255.0"
    assert prefix_to_netmask(25) == "255.255.255.128"
    assert prefix_to_netmask(16) == "255.255.0.0"


def test_round_trip_masks():
    for prefix in range(0, 33):
        mask = prefix_to_netmask(prefix)
        assert parse_cidr_prefix(mask) == prefix
