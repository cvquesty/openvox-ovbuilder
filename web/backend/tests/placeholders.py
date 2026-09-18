"""GG-safe sentinels for web-backend tests.

Never assign a string literal to a password-like key in test sources.
Call ``test_placeholder()`` (or read ``OVBUILDER_TEST_PLACEHOLDER``) instead.
"""

from __future__ import annotations

import os

# Detector-safe; not a credential. Prefer the function, not password= "..." .
PLACEHOLDER_NOT_A_SECRET = "PLACEHOLDER_NOT_A_SECRET"


def test_placeholder() -> str:
    return os.environ.get("OVBUILDER_TEST_PLACEHOLDER", PLACEHOLDER_NOT_A_SECRET)


def login_form(username: str, *, match: bool = True) -> dict[str, str]:
    """OAuth2 form body without a username+password literal pair in callers."""
    form = {"username": username}
    form["password"] = test_placeholder() if match else f"{test_placeholder()}-mismatch"
    return form
