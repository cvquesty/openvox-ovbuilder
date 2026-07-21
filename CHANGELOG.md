# Changelog

All notable changes to ovbuilder will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.97-beta4] - 2026-07-21

### Fixed
- **Alma/Ubuntu golden clones: static IP not applied** — interview network
  was only embedded under user-data `network:`, which VMware cloud-init
  does not use for early network config. NM still ran (profile rename
  like "cloud-init ens33") without addresses. Network Config v2 now goes
  in **metadata** (`network` + `network.encoding: base64`) and
  `guestinfo.networkconfig` per cloud-init VMware datasource docs.

## [0.97-beta3] - 2026-07-21

### Fixed
- **Terraform duplicate `guest_id`** in root `variables.tf` (clone-mode
  declaration collided with legacy ISO one) — blocked `ovbuilder build`
  at plan with "Duplicate variable declaration".

## [0.97-beta2] - 2026-07-21

### Fixed
- **`ModuleNotFoundError: No module named 'ovbuilder.main'`** after system
  install: pip preserved source modes `750`/`640` under root:wheel, so the
  normal user could not read site-packages. `install.sh` now forces `umask
  022` for pip, `chmod -R a+rX` immediately after install, and verifies
  `from ovbuilder.main import cli` as the invoking user before success.

## [0.97-beta1] - 2026-07-21

### Review release (correctness, DRY, docs)
- Full codebase review: correctness, de-duplication, best practices.
- **vSphere**: shared DC lookup + task waiter (no busy-spin on ISO browse);
  extensive module docs for connect/list/disconnect-media.
- **build**: removed dead OptionInfo loop; extracted table helpers; golden
  agent bootstrap skips re-network (cloud-init already applied).
- **ssh**: no invented default gateway/DNS; optional `configure_network`;
  shell-token safety checks on identity fields.
- **terraform driver**: SSL allow flag from config (not hard-coded only);
  documented per-VM state isolation rationale.
- **network / cloud_init / config / main / version**: line-level documentation
  of contracts and failure modes.
- Terraform module header comments for clone vs iso lifecycle.
- Version train jumps to **0.97-beta1** (operator-requested beta tag).

## [0.3.0-dev.2] - 2026-07-21

### Fixed
- **Install ISO left mounted after Terraform create** locked the media on the
  datastore and could re-boot the installer on reboot.
  - New `vsphere.disconnect_install_media()` reconfigures CD/DVD to a
    disconnected client device (no datastore ISO) and prefers disk boot.
  - ISO-mode flow: after OS install confirmation, ovbuilder **detaches media
    before** optional SSH/agent steps (and before you reboot into the OS).
  - Golden clone path: post-clone hygiene detach of any leftover CD.
  - Terraform `from_iso` uses `lifecycle.ignore_changes = [cdrom]` so a later
    apply does not re-attach and re-lock the ISO.

## [0.3.0-dev.1] - 2026-07-21

### Added
- **Packer golden images** for **AlmaLinux 10** and **Ubuntu 24.04**
  (`packer/almalinux-10`, `packer/ubuntu-24.04`) with shared cleanup scripts
  and VMware cloud-init guestinfo datasource config.
- **Default provision mode: `golden`** — select OS → interview (hostname, IP,
  CIDR, gateway, DNS, sizing) → Terraform **clones the Packer template** and
  injects identity via cloud-init guestinfo. VM is ready for SSH login;
  OpenVox agent install is optional after ACLs allow access to the server.
- Config keys: `provision_mode`, `golden_images` (template name, guest_id,
  default_user per OS).
- CLI: `--os`, `--mode golden|iso`, `--gateway`, `--dns`.
- Terraform module dual-mode: `clone` (template) vs `iso` (legacy).

### Docs
- `packer/README.md` — build prerequisites, vars, rebuild cadence.
- Operator flow documented in main README.

## [0.2.0-dev.22] - 2026-07-21

### Fixed
- **`install.sh` no longer deletes Terraform state** when restaging modules.
  Reinstall used `rm -rf /opt/ovbuilder/terraform`, which wiped the shared
  `terraform.tfstate` (and would lose tracking of existing VMs). State files,
  `.terraform/`, and the lockfile are now preserved across upgrades.

## [0.2.0-dev.21] - 2026-07-21

### Fixed
- **Building a new VM no longer renames/overwrites the previous one.**  
  Root cause: a single shared `terraform/terraform.tfstate` tracked one
  `module.vm` resource. Changing `vm_name` from `ovca2` → `ovca3` was an
  in-place update (vSphere rename), not a create.  
  **Fix:** each hostname gets isolated state under
  `~/.local/share/ovbuilder/tfstate/<vm_name>/terraform.tfstate`.  
  Legacy shared state is migrated once into the per-VM path for the VM it
  currently tracks.

### Added
- Clear console messages showing which state file apply uses.
- `run_terraform_destroy()` helper for per-VM teardown (same state path).

## [0.2.0-dev.20] - 2026-07-21

### Fixed
- Interactive subnet prompt no longer crashes with `ValueError: invalid literal for int() ... '/19'`
  when the operator types a leading slash (or a full CIDR / dotted netmask).
- Any IPv4 prefix **0–32** is accepted — not limited to a short list of “common” values.
  Valid forms: `19`, `/19`, `10.0.0.0/19`, `255.255.224.0`.
- Invalid input re-prompts with a clear error instead of aborting the whole build.

### Added
- `ovbuilder.network.parse_cidr_prefix` / `prefix_to_netmask` helpers + unit tests.
- CLI flags `--prefix` / `--cidr` for non-interactive builds (same free-form parsing).

## [0.2.0-dev.19] - 2026-07-15

### Changed
- Interactive network prompts: replaced cryptic "Prefix (CIDR)" with a short explanation and
  "Subnet prefix length (e.g. 24 for /24)" (what the `/24` in `10.0.42.10/24` means).
  Gateway/DNS labels clarified as IP addresses.

## [0.2.0-dev.18] - 2026-07-15

### Fixed
- IndentationError in `vsphere.list_isos` introduced during merge (duplicate loop header).

## [0.2.0-dev.17] - 2026-07-15

### Fixed
- Interactive ISO selection falls back to curated `known_isos` (with clear messaging) when live datastore browse returns no files.
- ISO path discovery strips the `[datastore]` prefix so Terraform receives a clean relative path for the CD-ROM block.
- README no longer dumps long install-permission troubleshooting in the Quick Start path; recovery notes live under Troubleshooting.
- Module prefers CD-ROM boot via `bios.bootDeviceClasses` alongside attach + boot delay.

### Changed
- Documented live vSphere discovery flow end-to-end; version aligned to `0.2.0-dev.17` (past prior `v0.2.0-dev.*` tags whose VERSION file had drifted).

## [0.2.0-dev.4] - 2026-06-24

### Fixed
- Bare `ovbuilder` (no subcommand) crashed with `TypeError: 'OptionInfo' and 'int'` on `memory * 1024` (and similar for cpus/disk).
  - Root cause: the default-to-build path in `main.py` calls `build_command(ctx)` directly. Typer does not process `Option()` defaults, so parameters receive raw `typer.models.OptionInfo` objects.
  - Hardened the direct-invoke path: OptionInfo guards now use both `isinstance` and `type().__name__` check; added `_coerce_sizing()` normalization right before arithmetic and var building so None or leaked OptionInfo always become the safe cfg defaults.
- Removed stray unconditional `vsphere_user = None; vsphere_password = None` that would have broken flag-passed credentials in non-interactive use.
- vSphere credentials entered during interactive prompts were not reaching Terraform (`Missing required argument "user"/"password"`, "Invalid provider configuration").
  - The long interactive block + separate passing of user/pass made it easy to drop the values.
  - Fix: populate auth into vm_vars defensively, add final carry step right before the terraform call, and (most importantly) the driver now always supplies `vsphere_user`, `vsphere_password`, and `vsphere_server` via *explicit* `-var=...` on the command line + TF_VAR_* + VSPHERE_* env vars. This guarantees the root provider block receives them.

## [0.2.0-dev.3] - 2026-06-24

### Fixed
- Installer now proactively ensures the installed `ovbuilder` command is executable by normal users when run with sudo. It detects sudo usage and uses a fallback `chmod +x || sudo chmod +x` on both the symlink and real target (using readlink -f), plus the python interpreter(s). This directly addresses repeated "zsh: permission denied: ovbuilder" after `sudo ./install.sh` on macOS.
- Strengthened post-install permission block to run reliably regardless of previous uninstall/reinstall state.

## [0.2.0-dev.2] - 2026-06-24

### Fixed
- Installer now aggressively detects sudo (via USE_SUDO and EUID) and explicitly `sudo chmod 755` the real target binary, symlink, venv bin dir, and python interpreters *after* creating the symlink. This ensures `ovbuilder` is executable by the normal user even after `sudo ./install.sh`.
- Added `readlink -f` + targeted chmod + `find` to cover umask issues common on macOS with sudo.
- Improved uninstall to always clean both system and user locations.
- Better post-install messaging with exact commands for PATH and `hash -r`.
- README updated with sudo -H recommendation and immediate fix commands for "permission denied" after sudo.

## [0.2.0-dev.1] - 2026-06-24

### Added
- `install.sh` installer script that automatically discovers suitable Python 3, creates dedicated venv, uses venv's pip (no reliance on bare `pip` command), installs package, and creates /usr/local/bin/ovbuilder symlink. Supports --user, -y, --uninstall.
- `AGENTS.md` consolidating governance from openvox-gui (version discipline, pre-commit checklist, /commit skill usage, SemVer + pre-releases, heredoc safety, etc.) to ensure consistent practices.
- Full sanitization of all documentation and code examples to remove references to twitter, X, xAI, or SpaceX (replaced with example.com, generic names like "Main DC").

### Changed
- Updated README.md Quick Start to use `./install.sh` for easier onboarding (especially for users not regularly using Python/pip).
- Added manual/development install section and notes on no-Python-experience design.
- Sanitized config defaults, examples, and terraform module files for generic domains and names.
- Updated CHANGELOG and governance to follow openvox-gui established requirements.

### Documentation
- Gathered and internalized all governance documents from openvox-gui (AGENTS.md, ovox README, bump-version.sh, CONTRIBUTING.md, etc.).
- Ensured version increments, commit skill usage, and pre-commit checklist will be followed.

See the [README](README.md) for usage.

## [0.1.0] - 2026-06-24

### Added
- Initial release of `ovbuilder`
- Interactive and flag-driven `ovbuilder build` command
- OS/ISO selector with curated list
- Prompts for hostname, IP, CPU, memory, disk (always thin provisioned)
- Drives bundled Terraform module for VMware vSphere ISO boot VMs
- Post-install SSH automation: hostname/IP config + OpenVox agent bootstrap via `curl ... | sudo bash`
- XDG config (`~/.config/ovbuilder/`) following ovox conventions
- Rich TUI experience with Typer + Rich (badges, panels, tables, emojis)
- Self-contained with bundled Terraform provisioning module
- Full support for the OpenVox ecosystem (registration to openvox server)

### Documentation
- Modern, enjoyable README with version panels, stack badges, and flow imagery

See the [README](README.md) for usage.
