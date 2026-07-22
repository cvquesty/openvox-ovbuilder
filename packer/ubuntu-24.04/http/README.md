# Ubuntu autoinstall seed

- **Source of truth:** `user-data.pkrtpl` + empty `meta-data`
- Passwords from gitignored pkrvars: `ssh_password`, `ssh_password_crypted`
  (`openssl passwd -6 '...'`). See `docs/SECRETS.md`.
