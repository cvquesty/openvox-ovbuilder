# Changelog

All notable changes to ovbuilder will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
