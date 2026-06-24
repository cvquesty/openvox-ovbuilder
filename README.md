<div align="center">

# 🦊 ovbuilder

**The OpenVox-native CLI for building VMware VMs from ISO images — fast, repeatable, and a little bit magical.**

[![Version](https://img.shields.io/badge/version-0.2.0--dev.3-orange?style=for-the-badge)](https://github.com/cvquesty/openvox-ovbuilder/releases)
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

Think of it as the friendly older sibling to raw `terraform apply` — it handles the ISO dance, the vSphere ceremony, the "wait, did I set the IP right?" moment, and then reaches inside the fresh VM to run the official OpenVox agent bootstrap so your new box phones home immediately.

## 🎯 What is ovbuilder?

If you've ever found yourself:

- Hunting for the right ISO path in a datastore
- Copy-pasting the same Terraform variables for the 47th time
- SSHing in after the OS install just to run that one curl | sudo bash command
- Forgetting whether you used thin provisioning this time

...then ovbuilder is for you.

It follows the same modern, noun-verb, operator-first philosophy as the `ovox` CLI. Everything is intentional, discoverable, and a little bit enjoyable.

## 📦 Current Components & Versions

These are the moving parts that make up ovbuilder today (verified in a live RHEL 9 + VMware environment):

| Component              | Version     | Notes |
|------------------------|-------------|-------|
| **ovbuilder CLI**      | `0.1.0`     | Typer + Rich Python package. The friendly face. |
| **Terraform Module**   | 1.x (bundled) | VMware vSphere ISO boot + thin disks + EFI. Lives in `terraform/modules/vm/`. |
| **Python Runtime**     | 3.9+        | Typer ≥0.12, Rich ≥13, Paramiko ≥3 for SSH post-steps. |
| **Post-Install**       | —           | Hostname + static IP (nmcli/netplan best-effort) + official OpenVox `install.bash`. |
| **OpenVox Target**     | 8.x+        | Registers via the standard `curl -k --noproxy ... | sudo bash` flow. |

The bundled Terraform module always uses **thin provisioning** for disks and disables guest waiters (because a fresh ISO installer isn't a "ready" guest yet).

## 🖼️ The ovbuilder Experience (in pictures and emojis)

**The happy path looks like this:**

```
🦊 ovbuilder build
    ↓ (interactive OS selector + prompts)
    ↓ (collects vSphere creds like a pro)
    ↓ (fires Terraform with correct vars)
🛠️  VM created + ISO attached + powered on
    ↓ (you finish the OS install in the vSphere console)
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

# Run the installer (recommended).
# Use sudo -H on macOS to avoid HOME/permission issues.
sudo -H ./install.sh

# Run the builder — it should just work
ovbuilder build
```

### After Installation

You should be able to run:

```bash
ovbuilder build
```

**From your ls output (this is the exact problem):**

The symlink `/usr/local/bin/ovbuilder` has `lrwxr-x---` (0750) — other users have no permissions on the link.

`pyvenv.cfg` has `-rw-r-----` (0640) — no read for your normal user.

The dirs are 755 and the wrapper is 755 (good).

**Immediate fix for your current state:**

```bash
sudo chmod -h 755 /usr/local/bin/ovbuilder
sudo chmod 644 /opt/ovbuilder/venv/pyvenv.cfg
sudo chmod 755 /opt/ovbuilder/venv /opt/ovbuilder/venv/bin

export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
hash -r
ovbuilder build
```

**If your source checkout still has the bad `ovbuilder.egg-info` (0750 root:staff):**

```bash
sudo chown -R $(id -un):staff .
sudo chmod -R u+rwX .
```

**Pull the latest script and re-install:**

```bash
git fetch origin
git reset --hard origin/staging
sudo -H ./install.sh
```

The script now ends with a block that always does the `chmod +x || sudo chmod +x` (with -h for the link) and the a+rX for the venv.

It will print the exact location and the commands to run in this shell.

The "which not found" is zsh's hash cache after the permission denied — `hash -r` clears it.

Run:

```bash
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
hash -r
ovbuilder build
```

For it to persist, add the export line above to your `~/.zshrc` (default on modern macOS) and restart your terminal, or run `source ~/.zshrc`.

**Critical fix in the current installer:** For sudo/system installs we now do a *regular* (non-editable) `pip install .` instead of `-e .`.

This prevents pip from creating any files in your source checkout as root (e.g. the `ovbuilder.egg-info` directory with 0750 root:staff permissions you observed). Root-owned files in the source tree prevent your normal user from descending into directories, causing "permission denied" or "not found" even for the installed command.

Editable mode is only used for user-mode (`--user`) installs.

The installer always ends with aggressive permission fixes (`chmod +x || sudo chmod +x`) on the symlink and real target.

If you are still seeing the error right now, fix your source tree and re-install:

```bash
cd /path/to/your/openvox-ovbuilder-clone
sudo chown -R $(id -un):staff .
sudo chmod -R u+rwX .

sudo -H ./install.sh
```

Then in the same shell:

```bash
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
hash -r
ovbuilder build
```

If permission denied persists on the command:

```bash
sudo chmod +x /usr/local/bin/ovbuilder
sudo chmod +x /opt/ovbuilder/venv/bin/ovbuilder 2>/dev/null || true
sudo chmod +x /opt/ovbuilder/venv/bin/python* 2>/dev/null || true
hash -r
ovbuilder build
```

### Manual / Development Install

If you prefer to manage the environment yourself:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

During the flow you'll pick an ISO, give it a name and IP, choose some sizing, watch Terraform do its thing, finish the OS install in the console, then let ovbuilder finish the registration for you.

Non-interactive is also fully supported for scripts and CI:

```bash
ovbuilder build \
  --hostname openvox-web03 \
  --ip 10.0.42.103 \
  --iso isos/AlmaLinux-9.4-x86_64-dvd.iso \
  --cpus 4 --memory 8 --disk 120 \
  --yes
```

## ✨ Features You'll Actually Use

- **Gorgeous interactive mode** with OS selector table and smart defaults
- **Everything is thin provisioned** — no fat disks by accident
- **Real post-install magic** — hostname, IP, and the OpenVox agent bootstrap over SSH
- **Self-contained** — includes its own Terraform module + `install.sh`
- **Beginner-friendly for non-Python users** — the installer locates a working Python 3, creates a venv, and sets up the command for you. No need to type `pip` manually.
- **vCenter discovery** — in interactive mode, you provide vCenter login once; ovbuilder connects, discovers real datacenters/clusters/datastores/networks/ISOs from your environment, and uses them to populate friendly selection menus (no more guessing "Main DC" or "VM Production" that don't exist in your vCenter). Data is used live for that run (caching can be added later).
- **Follows the ovox design language** — same config locations, same vibe, same "it just feels right" feeling
- **College-junior friendly docs** — we explain the "why" without talking down to you

## 📚 How It Works (the interesting bits)

1. You answer a few questions (or pass flags).
2. ovbuilder assembles the right `TF_VAR_*` values and runs `terraform apply` against the bundled module.
3. The VM boots your chosen ISO with a sensible boot delay and the CD-ROM attached.
4. You complete the OS install in the vSphere console (set the hostname and IP you gave ovbuilder — or let the post-step try to fix it).
5. ovbuilder waits for SSH, connects, configures what it can, and runs the official OpenVox registration script.
6. Your new node phones home and is ready for the ENC, Hiera, or whatever classification you use.

No more "did I remember to run the curl command?"

## 🛠️ Configuration

ovbuilder uses the same XDG conventions as `ovox`:

- Config: `~/.config/ovbuilder/config.yaml`
- (No tokens yet — vSphere credentials are collected interactively or via `TF_VAR_vsphere_*`)

Example config:

```yaml
terraform_dir: ""   # leave blank to use the bundled ./terraform
vm_datastore: vsanDatastore
iso_datastore: isos
networks:
  - "VM Production"
openvox_server: openvox.example.com
default_cpus: 2
default_memory_gb: 4
default_disk_gb: 80

known_isos:
  "AlmaLinux 9.4": "isos/AlmaLinux-9.4-x86_64-dvd.iso"
  "Ubuntu 24.04": "isos/ubuntu-24.04-live-server-amd64.iso"
```

Environment variables win (prefixed `OVBUILDER_`).

## 🔧 Requirements

- Python 3.9+
- Terraform 1.5+ (in your PATH)
- Network access to your vCenter
- Ability to SSH to newly built VMs (after OS install)
- An ISO collection on a datastore that your Terraform account can see

## 🤝 Contributing

We love contributions that make the "I need a new node" flow even smoother.

- Follow the same code style as the ovox CLI (clean, tested where possible, great help text).
- New features should have both interactive and `--yes` paths.
- Documentation should stay enjoyable but informative (college-junior voice).

See [CHANGELOG.md](CHANGELOG.md) for release process notes.

## 📜 License

Apache 2.0 — same as the rest of the OpenVox ecosystem.

---

Made with 🦊 and a healthy disrespect for repetitive manual labor.

*Part of the OpenVox family of tools.*
