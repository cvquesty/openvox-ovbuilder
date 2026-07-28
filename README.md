<div align="center">

# ovbuilder

**Build OpenVox-ready VMware VMs from Packer golden templates (or a legacy ISO) — without memorizing Terraform every time.**

[![Version](https://img.shields.io/badge/version-0.97--beta17-orange?style=for-the-badge)](https://github.com/cvquesty/openvox-ovbuilder/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Terraform](https://img.shields.io/badge/Terraform-1.5%2B-7B42BC?style=for-the-badge&logo=terraform&logoColor=white)](https://terraform.io)

[Quick Start](#quick-start) · [Install](#install) · [Usage](#usage) · [Configuration](#configuration) · [Secrets](docs/SECRETS.md) · [Changelog](CHANGELOG.md)

</div>

---

## What is ovbuilder?

ovbuilder is a command-line tool. You run it on **your laptop or workstation** (macOS, Linux, or Windows). It talks to **VMware vCenter**, creates a virtual machine, sets hostname and IP, and can install the OpenVox agent so the new node joins your Puppet/OpenVox fleet.

You do **not** need to be a UNIX expert. If you can open a terminal (Terminal.app, PowerShell, Windows Terminal, or Git Bash) and type a few commands, you can use it.

Typical flow:

1. Build golden OS templates once with Packer (optional but recommended).
2. Run `ovbuilder build`.
3. Answer prompts (vCenter login, datacenter, OS, hostname, IP, size).
4. Terraform clones the template. The guest boots with cloud-init identity.
5. Optionally install the OpenVox agent over SSH.

Legacy mode still supports empty-disk + ISO install if you prefer console installs.

## Secrets first

**Never commit passwords or vCenter credentials to git.**

Set up a local golden-login password before your first clone. Full steps for macOS, Linux, and Windows:

→ **[docs/SECRETS.md](docs/SECRETS.md)**

## Requirements

| Tool | Why | Where to get it |
|------|-----|-----------------|
| **Python 3.9+** | Runs the `ovbuilder` CLI | [python.org](https://www.python.org/downloads/) or your OS package manager |
| **Terraform 1.5+** | Creates/clones the VM in vSphere | [HashiCorp Terraform](https://developer.hashicorp.com/terraform/install) |
| **Network to vCenter** | Inventory discovery + Terraform | Your VPN / lab network |
| **Packer 1.9+** (optional) | One-time golden image builds | [HashiCorp Packer](https://developer.hashicorp.com/packer/install) |
| **Git** | Clone this repository | [git-scm.com](https://git-scm.com/) |

Guest VMs are Linux (AlmaLinux / Ubuntu). The **operator machine** can be UNIX or Windows.

## Install

### macOS or Linux (recommended)

```bash
git clone https://github.com/cvquesty/openvox-ovbuilder.git
cd openvox-ovbuilder

# macOS: sudo -H keeps your real HOME so config lands in the right place
sudo -H ./install.sh
```

Then open a **new** terminal (or refresh your PATH) and run:

```bash
ovbuilder --version
```

User-only install (no `/opt`):

```bash
./install.sh --user
```

Uninstall:

```bash
./install.sh --uninstall
```

### Windows

`install.sh` is a Bash script. On Windows use one of:

1. **WSL (Windows Subsystem for Linux)** — clone and run `./install.sh` inside WSL (treat as Linux).
2. **Git Bash** — sometimes works for `install.sh`; if not, use the manual steps below.
3. **PowerShell / Command Prompt** — manual Python venv (works everywhere):

```powershell
git clone https://github.com/cvquesty/openvox-ovbuilder.git
cd openvox-ovbuilder

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .

# Put Terraform on PATH, then:
ovbuilder --version
```

If PowerShell blocks script activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Add the venv `Scripts` folder to your user PATH, or activate the venv each session.

### Manual / development install (any OS)

**macOS / Linux / WSL / Git Bash:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
ovbuilder build
```

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
ovbuilder build
```

## Quick start

1. Install Terraform and put it on your `PATH`.
2. Create [secrets](docs/SECRETS.md) (`OVBUILDER_GOLDEN_PASSWORD`).
3. Build Packer goldens once (see [packer/README.md](packer/README.md)), **or** skip ahead if templates already exist in vCenter.
4. Run:

```bash
ovbuilder build
```

You will be asked for:

- vCenter hostname and login
- Datacenter, cluster, datastore, network(s)
- OS (golden template), or ISO if you chose legacy mode
- Hostname, IP, subnet prefix, optional gateway/DNS
- CPU, memory (GB), disk (GB, always thin-provisioned)

Confirm, then Terraform runs. When it finishes, the VM is cloned and powered on.

### Golden path (default)

```text
ovbuilder build
  → discover vSphere inventory
  → select OS (AlmaLinux 10 / Ubuntu 24.04 template)
  → interview hostname / IP / sizing
  → terraform clone + cloud-init guestinfo
  → cloud-init sets identity + (on Alma) DNF groups
  → SSH-ready guest
  → optional OpenVox agent bootstrap
```

### Legacy ISO path

```bash
ovbuilder build --mode iso
```

```text
ovbuilder build --mode iso
  → pick datastore ISO
  → empty disk + ISO attached
  → you finish OS install in the vSphere console
  → ovbuilder disconnects the ISO (releases the lock)
  → optional SSH network + DNF groups + agent bootstrap
```

## Usage

### Interactive (usual)

```bash
ovbuilder build
```

Same as running `ovbuilder` with no subcommand.

### Non-interactive golden (scripts / CI)

```bash
ovbuilder build --yes \
  --hostname openvox-web03 \
  --ip 10.0.42.103 \
  --os almalinux-10 \
  --prefix 24 \
  --gateway 10.0.42.1 \
  --dns 10.0.42.10 \
  --dns 1.1.1.1 \
  --cpus 4 --memory 8 --disk 120 \
  --vsphere-server vcenter.example.com \
  --vsphere-user administrator@vsphere.local \
  --vsphere-password "***"
```

Placement (datacenter, cluster, datastores, networks) comes from your config file unless you already set it interactively in a previous session and saved it.

### Non-interactive ISO

```bash
ovbuilder build --yes --mode iso \
  --hostname openvox-web03 \
  --ip 10.0.42.103 \
  --iso isos/AlmaLinux-10.0-x86_64-dvd.iso \
  --vsphere-server vcenter.example.com \
  --vsphere-user administrator@vsphere.local \
  --vsphere-password "***"
```

### Useful flags

| Flag | Meaning |
|------|---------|
| `--mode golden` / `--mode iso` | Clone template (default) or attach ISO |
| `--os almalinux-10` | Golden image key (non-interactive golden) |
| `--iso path/to.iso` | Datastore-relative ISO path (ISO mode) |
| `--hostname` / `--ip` | Guest identity |
| `--prefix` / `--cidr` | Subnet as `24`, `/19`, or `255.255.224.0` |
| `--gateway` | Optional default gateway |
| `--dns` | DNS server (repeat or comma-separate; interactive: one per prompt until empty) |
| `--cpus` / `--memory` / `--disk` | Size (memory in **GB**) |
| `--skip-dnf-groups` | Do not install configured EL package groups |
| `--yes` / `-y` | No prompts (requires hostname, IP, and OS or ISO) |
| `-V` / `--version` | Print version |

```bash
ovbuilder --help
ovbuilder build --help
ovbuilder config
```

## How it works

| Piece | Role |
|-------|------|
| `ovbuilder` CLI | Interview, discovery, Terraform driver, optional SSH |
| Bundled Terraform (`terraform/modules/vm`) | Clone golden **or** empty disk + ISO |
| Per-VM state | Each hostname has its own Terraform state file |
| cloud-init guestinfo | Hostname + static IP on golden clones |
| DNF groups | Extra EL package groups after network is up |
| SSH post-steps | Optional agent install; ISO network + DNF groups |

State lives under the **data directory** (see [Configuration](#configuration)), not in the shared Terraform module folder. Building `ovca3` does not rename `ovca2`.

## Configuration

### Where files live

| OS | Config + secrets | Terraform state / data |
|----|------------------|------------------------|
| **Linux / macOS** | `~/.config/ovbuilder/` | `~/.local/share/ovbuilder/` |
| **Windows** | `%APPDATA%\ovbuilder\` | `%LOCALAPPDATA%\ovbuilder\` |
| **Any OS** | `$XDG_CONFIG_HOME/ovbuilder/` if set | `$XDG_DATA_HOME/ovbuilder/` if set |

Examples:

- macOS: `/Users/you/.config/ovbuilder/config.yaml`
- Linux: `/home/you/.config/ovbuilder/config.yaml`
- Windows: `C:\Users\you\AppData\Roaming\ovbuilder\config.yaml`

Create the directory if it does not exist. vSphere passwords are **not** written to `config.yaml` by default.

### Example `config.yaml`

```yaml
terraform_dir: ""   # blank = bundled or /opt/ovbuilder/terraform
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
provision_mode: golden   # or iso

golden_images:
  almalinux-10:
    template: ovbuilder-almalinux-10
    guest_id: other4xLinux64Guest
    default_user: almalinux
    description: AlmaLinux 10 (Packer golden)
  ubuntu-24.04:
    template: ovbuilder-ubuntu-24.04
    guest_id: ubuntu64Guest
    default_user: ubuntu
    description: Ubuntu 24.04 LTS (Packer golden)

# EL only. Applied at clone time (cloud-init) or ISO SSH post-install.
# Set to [] to disable. One-off skip: --skip-dnf-groups
dnf_groups:
  - Server
  - Virtualization Host
  - Console Internet Tools
  - Container Management
  - RPM Development Tools
  - Development Tools
  - Headless Management
  - Legacy UNIX Compatibility
  - Network Servers
  - Scientific Support
  - Security Tools
  - System Tools

known_isos:
  "AlmaLinux 10": "isos/AlmaLinux-10.0-x86_64-dvd.iso"
  "Ubuntu 24.04": "isos/ubuntu-24.04-live-server-amd64.iso"
```

### Environment overrides

| Variable | Purpose |
|----------|---------|
| `OVBUILDER_TERRAFORM_DIR` | Terraform root module path |
| `OVBUILDER_VM_DATASTORE` | Default VM datastore |
| `OVBUILDER_ISO_DATASTORE` | Default ISO datastore |
| `OVBUILDER_OPENVOX_SERVER` | OpenVox compile/CA host for agent install |
| `OVBUILDER_PROVISION_MODE` | `golden` or `iso` |
| `OVBUILDER_GOLDEN_PASSWORD` | Guest login password (see SECRETS.md) |
| `XDG_CONFIG_HOME` / `XDG_DATA_HOME` | Override config/data roots on any OS |

```bash
ovbuilder config
```

## DNF groups (AlmaLinux / RHEL family)

Golden and ISO guests often start as **Minimal** / `@core`. By default ovbuilder installs a set of DNF groups after the network is configured:

- Golden: cloud-init runs `/usr/local/sbin/ovbuilder-dnf-groups.sh`
- ISO: SSH post-install runs the same logic
- Ubuntu: skipped (no `dnf` / `yum`)

Edit `dnf_groups` in config, or pass `--skip-dnf-groups` for a lean clone. Failed group names are warnings; the build continues.

First boot on Alma can take a while while groups download.

## Current versions

| Component | Version / note |
|-----------|----------------|
| ovbuilder CLI | `0.97-beta17` |
| Terraform module | Bundled `terraform/modules/vm` (clone + ISO) |
| Python | 3.9+ |
| Guest targets | AlmaLinux 10, Ubuntu 24.04 (golden); other ISOs in ISO mode |
| OpenVox agent | 8.x+ via official `install.bash` on port 8140 |

## Troubleshooting

### `ovbuilder` not found after install (macOS / Linux)

```bash
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
hash -r
ovbuilder --version
```

If you used `sudo ./install.sh` without `-H` on macOS, re-run with `sudo -H ./install.sh`.

### Permission denied on the `ovbuilder` symlink (macOS)

```bash
sudo chmod -h 755 /usr/local/bin/ovbuilder
sudo chmod 644 /opt/ovbuilder/venv/pyvenv.cfg
sudo chmod 755 /opt/ovbuilder/venv /opt/ovbuilder/venv/bin
```

### Windows: `Activate.ps1` cannot be loaded

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### Menus show only generic defaults

- Confirm you installed the package that includes `pyvmomi` (`python -m pip show ovbuilder`).
- Interactive mode needs a working vCenter connection.

### Golden password missing

Follow the panel output or [docs/SECRETS.md](docs/SECRETS.md). The file must exist on the machine running `ovbuilder`, not inside the guest.

### Clone has no IP / cannot SSH

- Check cloud-init log on the guest console: `/var/log/cloud-init-output.log`
- Confirm gateway/DNS match the port group
- Confirm VMware Tools / `open-vm-tools` are running

### ISO still locked on the datastore

ovbuilder disconnects CD/DVD after clone hygiene and after ISO installs. If you exited early, disconnect manually in vSphere: VM → Edit Settings → CD/DVD → Client Device.

### Terraform “state lock” or wrong VM renamed

Each hostname has isolated state under the data directory `tfstate/<hostname>/`. Do not point two builds at the same hostname unless you intend to update that VM.

## Documentation map

| Doc | Audience |
|-----|----------|
| [README.md](README.md) (this file) | Everyone — install, use, configure |
| [docs/SECRETS.md](docs/SECRETS.md) | Everyone — passwords and credentials |
| [packer/README.md](packer/README.md) | People who build golden templates |
| [terraform/modules/vm/README.md](terraform/modules/vm/README.md) | People calling Terraform directly |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |
| [AGENTS.md](AGENTS.md) | Project versioning / commit rules |

## Contributing

- Keep CLI help clear for interactive and `--yes` users.
- Docs should stay beginner-friendly (college-freshman reading level) and work for Windows **and** UNIX operators.
- Versioning: see [CHANGELOG.md](CHANGELOG.md) and [AGENTS.md](AGENTS.md).

## License

Apache 2.0 — same as the rest of the OpenVox ecosystem.

---

Made with a healthy disrespect for repetitive manual labor.

*Part of the OpenVox family of tools.*
