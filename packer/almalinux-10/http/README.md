# Alma kickstart seed

- **Source of truth:** `ks.cfg.pkrtpl` (Packer `templatefile`)
- Password is injected from **gitignored** `packer/variables.auto.pkrvars.hcl`
  (`ssh_password`). See `docs/SECRETS.md`.
- Root is locked. `almalinux` is in `wheel` with password-required sudo
  (no NOPASSWD). Firewall allows SSH; SELinux is enforcing. Packer does
  not SSH during the build.
