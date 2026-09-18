# Ubuntu autoinstall seed

- **Source of truth:** `user-data.pkrtpl` + empty `meta-data`
- Passwords from gitignored pkrvars: `ssh_password`, `ssh_password_crypted`
  (`openssl passwd -6 '...'`). See `docs/SECRETS.md`.
- No well-known default password is baked into the seed. Root is locked
  (`disable_root: true`). The `ubuntu` user is in `sudo` and must enter the
  operator password (no NOPASSWD). Packer does not SSH during the build.
