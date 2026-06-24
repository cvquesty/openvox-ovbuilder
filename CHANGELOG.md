# Changelog

All notable changes to ovbuilder will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
