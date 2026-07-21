<div align="center">

# 🦊 ovbuilder

**The OpenVox-native CLI for building VMware VMs from ISO images — fast, repeatable, and a little bit magical.**

[![Version](https://img.shields.io/badge/version-0.97--beta1-orange?style=for-the-badge)](https://github.com/cvquesty/openvox-ovbuilder/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Typer](https://img.shields.io/badge/Typer-0.12+-blue?style=for-the-badge&logo=python&logoColor=white)](https://typer.tiangolo.com)
[![Rich](https://img.shields.io/badge/Rich-13+-brightgreen?style=for-the-badge&logo=python&logoColor=white)](https://rich.readthedocs.io)
[![Terraform](https://img.shields.io/badge/Terraform-1.5%2B-7B42BC?style=for-the-badge&logo=terraform&logoColor=white)](https://terraform.io)
[![Paramiko](https://img.shields.io/badge/Paramiko-3%2B-orange?style=for-the-badge&logo=python&logoColor=white)](https://paramiko.org)

[![GitHub Stars](https://img.shields.io/github/stars/cvquesty/openvox-ovbuilder?style=flat-square)](https://github.com/cvquesty/openvox-ovbuilder/stargazers)
[![GitHub Issues](https://img.shields.io/github/issues/cvquesty/openvox-ovbuilder?style=flat-square)](https://github.com/cvquesty/openvox-ovbuilder/issues)
[![Last Commit](https://img.shields.io/github/last-commit/cvquesty/openvox-ovbuilder?style=flat-square)](https://github.com/cvquesty/openvox-ovbuilder/commits/staging)

[Quick Start](#-quick-start) · [Usage](#-usage) · [Configuration](#-configuration) · [How It Works](#-how-it-works) · [Changelog](CHANGELOG.md) · [Contributing](#-contributing)

</div>

---

A delightful, standards-based command-line tool that turns "I need a new OpenVox node" into a few prompts and a finished, registered VM.

Think of it as the friendly older sibling to raw `terraform apply` — it discovers your vSphere inventory, lets you pick real datastores and ISOs, runs Terraform, then reaches inside the fresh VM to run the official OpenVox agent bootstrap so your new box phones home immediately.

## 🎯 What is ovbuilder?

If you've ever found yourself:

- Hunting for the right ISO path in a datastore
- Copy-pasting the same Terraform variables for the 47th time
- SSHing in after the OS install just to run that one curl | sudo bash command
- Forgetting whether you used thin provisioning this time

...then ovbuilder is for you.

It follows the same modern, noun-verb, operator-first philosophy as the `ovox` CLI. Everything is intentional, discoverable, and a little bit enjoyable.

## 📦 Current Components & Versions

| Component              | Version       | Notes |
|------------------------|---------------|-------|
| **ovbuilder CLI**      | `0.97-beta1` | Typer + Rich + pyVmomi. Live vCenter discovery. |
| **Terraform Module**   | 1.x (bundled) | VMware vSphere ISO boot + thin disks + EFI. Lives in `terraform/modules/vm/`. |
| **Python Runtime**     | 3.9+          | Typer ≥0.12, Rich ≥13, Paramiko ≥3, pyVmomi ≥8. |
| **Post-Install**       | —             | Hostname + static IP (nmcli/netplan best-effort) + official OpenVox `install.bash`. |
| **OpenVox Target**     | 8.x+          | Registers via the standard `curl -k --noproxy ... \| sudo bash` flow. |

The bundled Terraform module always uses **thin provisioning** for disks and disables guest waiters (because a fresh ISO installer isn't a "ready" guest yet).

## 🖼️ The ovbuilder Experience

```
🦊 ovbuilder build
    ↓ prompts for vCenter FQDN + credentials
    ↓ discovers datacenters, clusters, datastores, networks
    ↓ lists live .iso files from the ISO datastore you pick
    ↓ hostname / IP / sizing prompts
    ↓ terraform apply
🛠️  VM created + ISO attached + powered on
    ↓ you finish the OS install in the vSphere console
🔐  ovbuilder SSHes in
📡  Configures hostname + IP
📦  Runs the OpenVox agent installer
✅  Node appears in your fleet, ready for classification
```

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/cvquesty/openvox-ovbuilder.git
cd openvox-ovbuilder

# Install (recommended). On macOS use sudo -H so HOME stays correct.
sudo -H ./install.sh

# Interactive build — discovers inventory from vCenter
ovbuilder build
```

### Manual / Development Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
ovbuilder build
```

### Non-interactive (scripts / CI)

When you already know the inventory names:

```bash
ovbuilder build \
  --hostname openvox-web03 \
  --ip 10.0.42.103 \
  --iso isos/AlmaLinux-9.4-x86_64-dvd.iso \
  --cpus 4 --memory 8 --disk 120 \
  --vsphere-server vcenter.example.com \
  --vsphere-user administrator@vsphere.local \
  --vsphere-password '***' \
  --yes
```

In non-interactive mode, datacenter / cluster / datastores / networks come from `~/.config/ovbuilder/config.yaml` (or environment overrides). Interactive mode **overrides** those with what you select from live inventory.

## ✨ Features

- **Packer golden images** — AlmaLinux 10 + Ubuntu 24.04 templates (`packer/`)
- **Default golden clone path** — pick OS → interview → clone + cloud-init identity (SSH-ready)
- **Per-VM Terraform state** — building ovca3 never renames ovca2
- **Live vSphere discovery** — datacenters, clusters, datastores, networks via pyVmomi
- **Legacy ISO mode** still available (`--mode iso`)
- **Optional OpenVox agent bootstrap** over SSH after ACLs allow
- **Self-contained** — bundled Terraform module + `install.sh` + Packer defs
- **ovox design language** — Typer + Rich, XDG config, same vibe

## 📚 How It Works

### Golden path (default)

1. Build Packer templates once (`packer/README.md`) → `ovbuilder-almalinux-10` / `ovbuilder-ubuntu-24.04`.
2. `ovbuilder build` — credentials + inventory discovery.
3. **Select OS** (golden image) → interview hostname, IP, CIDR, gateway, DNS, sizing.
4. Terraform **clones** the template with **per-VM state** and injects cloud-init guestinfo.
5. VM boots; cloud-init applies hostname + static IP → **ready for SSH login**.
6. When firewall/ACLs allow, install the OpenVox agent (optional prompt or manual curl).

### Legacy ISO path (`--mode iso` or `provision_mode: iso`)

Empty disk + ISO attach → console OS install → optional SSH post-steps (as before).

## 🛠️ Configuration

XDG locations (same idea as `ovox`):

- Config: `~/.config/ovbuilder/config.yaml`
- Data: `~/.local/share/ovbuilder/` (includes per-VM Terraform state under `tfstate/`)

vSphere credentials are **not** stored by default — they are collected interactively or passed via flags / `TF_VAR_vsphere_*`.

Example config (defaults / non-interactive fallbacks):

```yaml
terraform_dir: ""   # blank = use bundled ./terraform (or /opt/ovbuilder/terraform after install)
vsphere_server: vcenter.example.com
vm_datastore: vsanDatastore
iso_datastore: isos
networks:
  - "VM Production"
datacenter: "Main DC"
cluster: "Production Cluster"
domain: example.com
openvox_server: openvox.example.com
default_cpus: 2
default_memory_gb: 4
default_disk_gb: 80

# Optional curated shortcuts (interactive mode prefers live ISO listing)
known_isos:
  "AlmaLinux 9.4": "isos/AlmaLinux-9.4-x86_64-dvd.iso"
  "Ubuntu 24.04": "isos/ubuntu-24.04-live-server-amd64.iso"
```

Environment overrides (prefix `OVBUILDER_`):

| Variable | Purpose |
|----------|---------|
| `OVBUILDER_TERRAFORM_DIR` | Override Terraform root module path |
| `OVBUILDER_VM_DATASTORE` | Default VM datastore name |
| `OVBUILDER_ISO_DATASTORE` | Default ISO datastore name |
| `OVBUILDER_OPENVox_SERVER` | OpenVox server for agent install |

Show effective config:

```bash
ovbuilder config
```

## 🔧 Requirements

- Python 3.9+
- Terraform 1.5+ on `PATH`
- Network access to vCenter
- Ability to SSH to newly built VMs after OS install
- ISO collection on a datastore your Terraform account can use

## 🩺 Troubleshooting

**Permission denied on `ovbuilder` after `sudo ./install.sh` (macOS):**

```bash
sudo chmod -h 755 /usr/local/bin/ovbuilder
sudo chmod 644 /opt/ovbuilder/venv/pyvenv.cfg
sudo chmod 755 /opt/ovbuilder/venv /opt/ovbuilder/venv/bin
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
hash -r
ovbuilder build
```

Re-install cleanly with `sudo -H ./install.sh` if the venv was created with a bad umask. Prefer non-editable system installs so root-owned `.egg-info` does not land in your source tree.

**Menus show only generic defaults (no live inventory):**

- Confirm you are running the installed package that includes `ovbuilder/vsphere.py` and `pyvmomi` (`pip show ovbuilder` / re-run `install.sh`).
- Interactive mode needs a working connection to vCenter; if discovery fails you will see a yellow fallback message and can type names manually.

**ISO selected but VM does not boot the installer:**

- Ensure `iso_path` is relative to the ISO datastore root (discovery already strips the `[datastore]` prefix).
- The module sets `boot_delay` and prefers CD-ROM in `extra_config`; still finish or accept the firmware boot menu if your environment requires it.

## 🤝 Contributing

- Follow the ovox CLI style (clean help text, interactive + `--yes` paths).
- Documentation should stay enjoyable but informative.
- See [CHANGELOG.md](CHANGELOG.md) and [AGENTS.md](AGENTS.md) for versioning and release process.

## 📜 License

Apache 2.0 — same as the rest of the OpenVox ecosystem.

---

Made with 🦊 and a healthy disrespect for repetitive manual labor.

*Part of the OpenVox family of tools.*
