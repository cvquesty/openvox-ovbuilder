"""Checks for the bare-metal web installer (no root, no live install)."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL_WEB = REPO / "web" / "install-web.sh"
NGINX = REPO / "web" / "nginx" / "ovbuilder.conf"
WEB_UNIT = REPO / "web" / "systemd" / "ovbuilder-web.service"
WORKER_UNIT = REPO / "web" / "systemd" / "ovbuilder-worker.service"
ENV_EXAMPLE = REPO / "web" / "backend" / ".env.example"


def test_install_web_self_test() -> None:
    result = subprocess.run(
        [str(INSTALL_WEB), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "self-test passed" in result.stdout


def test_install_web_help_no_root() -> None:
    result = subprocess.run(
        [str(INSTALL_WEB), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "install.sh" in result.stdout
    assert "--start" in result.stdout


def test_nginx_template_spa_and_api_proxy() -> None:
    text = NGINX.read_text(encoding="utf-8")
    assert "location /api/" in text
    assert "proxy_pass http://ovbuilder_api" in text
    assert "try_files $uri $uri/ /index.html" in text
    assert "__ROOT__/web/frontend/dist" in text
    assert "127.0.0.1:4567" in text


def test_systemd_units_use_venv_binaries() -> None:
    web = WEB_UNIT.read_text(encoding="utf-8")
    worker = WORKER_UNIT.read_text(encoding="utf-8")
    assert "uvicorn app.main:app" in web
    assert "--host 127.0.0.1 --port 4567" in web
    assert "celery -A app.tasks.celery_app worker" in worker
    assert "EnvironmentFile=-/opt/ovbuilder/web/backend/.env" in web
    assert "EnvironmentFile=-/opt/ovbuilder/web/backend/.env" in worker


def test_env_example_has_required_keys() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in (
        "SECRET_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "CORS_ORIGINS",
        "LDAP_SERVER_URL",
        "LDAP_BIND_DN",
    ):
        assert f"{key}=" in text
