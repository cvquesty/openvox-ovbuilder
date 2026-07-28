# Secrets — never commit

ovbuilder and Packer **must not** store real passwords, API tokens, or
vCenter credentials in the git repository. If they land in git, treat them
as burned: rotate immediately.

This guide covers **macOS, Linux, and Windows**.

## What lives where

| Secret | Local only (not in git) | Used by |
|--------|-------------------------|---------|
| Golden / clone login password | `secrets.env` or `OVBUILDER_GOLDEN_PASSWORD` | `ovbuilder build` (cloud-init guestinfo) |
| Packer vCenter + golden password | `packer/variables.auto.pkrvars.hcl` (gitignored) | `packer build` |
| vSphere password for ovbuilder | CLI prompt, `--vsphere-password`, or `VSPHERE_PASSWORD` / `TF_VAR_vsphere_password` | Terraform |

### Config directory (where `secrets.env` goes)

| Platform | Default directory |
|----------|-------------------|
| Linux / macOS | `~/.config/ovbuilder/` |
| Windows | `%APPDATA%\ovbuilder\` (usually `C:\Users\<you>\AppData\Roaming\ovbuilder\`) |
| Override (any OS) | `$XDG_CONFIG_HOME/ovbuilder/` |

File name: `secrets.env`

## Golden password (clones)

Every golden clone injects a guest login password via cloud-init. That
password must come from **your machine**, not from the repository.

### macOS / Linux / WSL / Git Bash

```bash
mkdir -p ~/.config/ovbuilder
chmod 700 ~/.config/ovbuilder
printf "%s\n" "OVBUILDER_GOLDEN_PASSWORD='choose-a-strong-lab-password'" > ~/.config/ovbuilder/secrets.env
chmod 600 ~/.config/ovbuilder/secrets.env
```

Or for one shell session only:

```bash
export OVBUILDER_GOLDEN_PASSWORD='choose-a-strong-lab-password'
ovbuilder build
```

### Windows PowerShell

```powershell
$dir = Join-Path $env:APPDATA "ovbuilder"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Set-Content -Path (Join-Path $dir "secrets.env") -Value "OVBUILDER_GOLDEN_PASSWORD=choose-a-strong-lab-password"
```

Or for one PowerShell session only:

```powershell
$env:OVBUILDER_GOLDEN_PASSWORD = 'choose-a-strong-lab-password'
ovbuilder build
```

### Optional YAML form

Same directory, file `secrets.yaml`:

```yaml
golden_password: choose-a-strong-lab-password
```

If the password is unset, golden clone **stops early** with setup instructions
instead of using a hard-coded default.

## Packer golden builds

Packer needs vCenter credentials and the same guest password you will use
for clones.

```bash
cd packer
cp variables.auto.pkrvars.hcl.example variables.auto.pkrvars.hcl
```

On Windows (PowerShell):

```powershell
cd packer
Copy-Item variables.auto.pkrvars.hcl.example variables.auto.pkrvars.hcl
```

Then edit `variables.auto.pkrvars.hcl`:

- `vcenter_server`, `vcenter_username`, `vcenter_password`
- `ssh_password` (plain) — same value as `OVBUILDER_GOLDEN_PASSWORD`
- `ssh_password_crypted` — generate with:

```bash
openssl passwd -6 'same-as-ssh_password'
```

On Windows, run `openssl` from Git Bash, WSL, or a local OpenSSL install.

Restrict file permissions when the OS supports it:

```bash
chmod 600 variables.auto.pkrvars.hcl
```

`*.pkrvars.hcl` is gitignored. Only `*.pkrvars.hcl.example` is tracked.

## If a secret was already pushed

1. **Rotate** it immediately (vCenter password, lab golden password, etc.).
2. Remove it from the working tree.
3. Optionally purge git history (`git filter-repo`) and force-push — only
   with team agreement. Still treat the old secret as compromised.

## Checklist before every push

```bash
git status
git grep -iE 'password\s*=\s*"|passwd|BEGIN (RSA |OPENSSH )?PRIVATE' -- ':!*.example' ':!docs/SECRETS.md' || true
```

No real passwords, no private keys, no production tokens.
