"""Golden template discovery: picker keys, labels, silent dedupe."""

from __future__ import annotations

from datetime import datetime, timezone

from ovbuilder.config import GoldenImage, OvbuilderConfig
from ovbuilder.goldens import (
    GoldenTemplate,
    dedupe_goldens,
    goldens_from_config,
    home_datacenter,
    human_label,
    infer_default_user,
    is_ovbuilder_template_name,
    match_golden,
    public_os_rows,
    resolve_os_image,
    templates_from_raw,
)


def test_home_datacenter_env_overrides_config(monkeypatch):
    monkeypatch.setenv("OVBUILDER_GOLDEN_HOME_DATACENTER", "PDXC")
    assert home_datacenter(OvbuilderConfig(golden_home_datacenter="ATLC")) == "PDXC"


def test_only_ovbuilder_prefix_is_selectable():
    assert is_ovbuilder_template_name("ovbuilder-ubuntu-24.04")
    assert not is_ovbuilder_template_name("ubuntu-24.04")
    assert not is_ovbuilder_template_name("windows-golden")
    assert not is_ovbuilder_template_name("")


def test_human_label_and_default_user_from_name():
    assert "Ubuntu" in human_label("ovbuilder-ubuntu-24.04")
    assert "24.04" in human_label("ovbuilder-ubuntu-24.04")
    assert infer_default_user("ovbuilder-ubuntu-24.04") == "ubuntu"
    assert infer_default_user("ovbuilder-almalinux-10") == "almalinux"


def test_config_defaults_are_ovbuilder_prefixed():
    rows = goldens_from_config(OvbuilderConfig())
    names = {r.name for r in rows}
    assert names == {"ovbuilder-ubuntu-24.04", "ovbuilder-almalinux-10"}
    keys = {r.key for r in rows}
    assert "ubuntu-24.04" in keys
    assert "almalinux-10" in keys


def test_config_excludes_non_ovbuilder_templates():
    cfg = OvbuilderConfig(
        golden_images={
            "other": GoldenImage(
                template="rhel-9-golden",
                default_user="root",
                description="Should not appear",
            ),
            "ubuntu-24.04": GoldenImage(
                template="ovbuilder-ubuntu-24.04",
                default_user="ubuntu",
                description="Ubuntu 24.04 LTS",
            ),
        }
    )
    rows = goldens_from_config(cfg)
    assert [r.name for r in rows] == ["ovbuilder-ubuntu-24.04"]


def test_public_rows_omit_datacenter_and_datastore():
    golden = GoldenTemplate(
        name="ovbuilder-ubuntu-24.04",
        datacenter="PDXC",
        datastore="HOTH_DEV",
        key="ubuntu-24.04",
        label="Ubuntu 24.04 LTS",
        default_user="ubuntu",
    )
    row = public_os_rows([golden])[0]
    assert "datacenter" not in row
    assert "datastore" not in row
    assert "uuid" not in row
    assert row["name"] == "ovbuilder-ubuntu-24.04"
    assert row["label"] == "Ubuntu 24.04 LTS"
    assert row["key"] == "ubuntu-24.04"


def test_dedupe_prefers_home_datacenter():
    older = GoldenTemplate(
        name="ovbuilder-ubuntu-24.04",
        datacenter="PDXC",
        created=datetime(2024, 1, 1),
    )
    newer = GoldenTemplate(
        name="ovbuilder-ubuntu-24.04",
        datacenter="SEA3 - Bellevue",
        created=datetime(2026, 1, 1),
    )
    rows = dedupe_goldens([newer, older], home_datacenter_name="PDXC")
    assert len(rows) == 1
    assert rows[0].datacenter == "PDXC"


def test_dedupe_prefers_newest_when_no_home():
    older = GoldenTemplate(
        name="ovbuilder-ubuntu-24.04",
        datacenter="ATLC",
        created=datetime(2024, 1, 1),
    )
    newer = GoldenTemplate(
        name="ovbuilder-ubuntu-24.04",
        datacenter="PDXC",
        created=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    rows = dedupe_goldens([older, newer], home_datacenter_name="")
    assert len(rows) == 1
    assert rows[0].datacenter == "PDXC"


def test_dedupe_stable_first_when_no_recency():
    a = GoldenTemplate(name="ovbuilder-ubuntu-24.04", datacenter="SEA3 - Bellevue")
    b = GoldenTemplate(name="ovbuilder-ubuntu-24.04", datacenter="PDXC")
    rows = dedupe_goldens([a, b], home_datacenter_name="")
    assert len(rows) == 1
    assert rows[0].datacenter == "PDXC"


def test_templates_from_raw_filters_and_enriches():
    raw = [
        {
            "name": "ovbuilder-ubuntu-24.04",
            "datacenter": "PDXC",
            "datastore": "HOTH_DEV",
            "guest_id": "ubuntu64Guest",
            "uuid": "u-1",
            "created": datetime(2026, 1, 1),
            "change_version": "",
        },
        {
            "name": "windows-golden",
            "datacenter": "PDXC",
            "guest_id": "windows9_64Guest",
        },
        {
            "name": "ovbuilder-ubuntu-24.04",
            "datacenter": "SEA3 - Bellevue",
            "created": datetime(2020, 1, 1),
        },
    ]
    cfg = OvbuilderConfig()
    rows = templates_from_raw(raw, cfg, home_datacenter_name="PDXC")
    assert len(rows) == 1
    assert rows[0].name == "ovbuilder-ubuntu-24.04"
    assert rows[0].datacenter == "PDXC"
    assert rows[0].key == "ubuntu-24.04"
    assert rows[0].default_user == "ubuntu"
    assert "Ubuntu" in rows[0].label


def test_match_accepts_key_and_full_name():
    goldens = goldens_from_config(OvbuilderConfig())
    assert match_golden("ubuntu-24.04", goldens).name == "ovbuilder-ubuntu-24.04"
    assert match_golden("ovbuilder-ubuntu-24.04", goldens).name == (
        "ovbuilder-ubuntu-24.04"
    )
    assert match_golden("almalinux-10", goldens).name == "ovbuilder-almalinux-10"
    assert match_golden("missing", goldens) is None


def test_resolve_os_image_prefers_live_over_config():
    live = [
        GoldenTemplate(
            name="ovbuilder-ubuntu-24.04",
            datacenter="PDXC",
            key="ubuntu-24.04",
            label="Ubuntu 24.04 LTS",
            default_user="ubuntu",
        )
    ]
    chosen = resolve_os_image("ubuntu-24.04", live, OvbuilderConfig())
    assert chosen.datacenter == "PDXC"


def test_resolve_os_image_unknown_raises():
    try:
        resolve_os_image("windows-11", [], OvbuilderConfig())
    except KeyError as exc:
        assert "windows-11" in str(exc)
        assert "ovbuilder-ubuntu-24.04" in str(exc)
    else:
        raise AssertionError("expected KeyError")
