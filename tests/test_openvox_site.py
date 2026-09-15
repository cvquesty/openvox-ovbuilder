"""Location → compiler VIP and no_proxy helpers."""

from ovbuilder.openvox_site import (
    DEFAULT_SITES,
    agent_install_command,
    infer_location,
    no_proxy_csv,
    parse_proxy_url,
    site_for,
)


def test_infer_location_from_domain():
    assert infer_location(domain="atlc-it.corp.int-x.ai") == "ATLC"
    assert infer_location(hostname="web1.pdxc-it.corp.int-x.ai") == "PDXC"


def test_infer_location_explicit_wins():
    assert infer_location(location="pdxc", domain="atlc-it.corp.int-x.ai") == "PDXC"


def test_site_for_atlc_uses_local_compiler():
    site = site_for("ATLC")
    assert site.compiler == "ovcompilers.atlc-it.corp.int-x.ai"
    assert site.gui == "openvox.atlc-it.corp.int-x.ai"
    assert site.ca_server == "ovca.corp.int-x.ai"


def test_site_for_pdxc_uses_local_compiler():
    site = site_for("PDXC")
    assert site.compiler == "ovcompilers.pdxc-it.corp.int-x.ai"


def test_agent_install_uses_gui_packages_and_clustered_flags():
    cmd = agent_install_command(DEFAULT_SITES["ATLC"])
    assert "openvox.atlc-it.corp.int-x.ai:4567/packages/install.bash" in cmd
    assert "--server ovcompilers.atlc-it.corp.int-x.ai" in cmd
    assert "--ca-server ovca.corp.int-x.ai" in cmd
    assert "--noproxy openvox.atlc-it.corp.int-x.ai,ovcompilers.atlc-it.corp.int-x.ai,ovca.corp.int-x.ai" in cmd


def test_no_proxy_includes_clone_identity():
    csv = no_proxy_csv(extra=["web1.pdxc-it.corp.int-x.ai", "172.29.32.10"])
    assert "web1.pdxc-it.corp.int-x.ai" in csv
    assert "172.29.32.10" in csv
    assert ".corp.int-x.ai" in csv


def test_parse_proxy_url_splits_auth():
    p = parse_proxy_url("http://user:secret@proxy.example.com:3128")
    assert p["host"] == "proxy.example.com"
    assert p["port"] == 3128
    assert p["username"] == "user"
    assert p["password"] == "secret"
