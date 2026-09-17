"""Ensure importing app.tasks / app.main during tests has a valid SECRET_KEY."""

from __future__ import annotations

import os

# Must be set before app.config.get_settings() is first called.
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-32ch")
os.environ.setdefault("DEBUG", "false")
