# Changelog

All notable changes to ovbuilder will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
