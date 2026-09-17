# Secrets — never commit

ovbuilder and Packer **must not** store real passwords, API tokens, or
vCenter credentials in the git repository. If they land in git, treat them
as burned: rotate immediately.

This guide covers **macOS, Linux, and Windows**.

## What lives where

| Secret | Local only (not in git) | Used by |
|--------|-------------------------|---------|
| Golden / clone login password | `secrets.env` or `OVBUILDER_GOLDEN_PASSWORD` | `ovbuilder build` (cloud-init guestinfo) |
| HTTP/HTTPS proxy (with auth) | `secrets.env` `OVBUILDER_HTTP_PROXY` | Clone-time apt/dnf/profile.d |
| Packer vCenter + golden password | `packer/variables.auto.pkrvars.hcl` (gitignored) | `packer build` |
| vSphere password for ovbuilder | CLI prompt, env (`VSPHERE_PASSWORD` / `OVBUILDER_VSPHERE_PASSWORD` / `TF_VAR_vsphere_password`), or `secrets.env`. Avoid `--vsphere-password` (visible in `ps`) | Terraform + web worker |
| Web API JWT secret | `web/backend/.env` `SECRET_KEY` (required unless `DEBUG=true`) | FastAPI |
| LDAP TLS CA | `LDAP_CA_CERTS_FILE` PEM path | Web LDAP bind |

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

## HTTP proxy (clone time)

Do **not** put the proxy password in git or Packer seeds. Add it to the
same `secrets.env`:

```bash
printf "%s\n" "OVBUILDER_HTTP_PROXY='http://USER:PASS@httpproxy.example.com:3128'" >> ~/.config/ovbuilder/secrets.env
chmod 600 ~/.config/ovbuilder/secrets.env
```

cloud-init writes `/etc/profile.d/ovbuilder-proxy.sh`, `/etc/environment`,
apt `01ovbuilder-proxy`, and dnf `dnf.conf.d/ovbuilder-proxy.conf`.
`no_proxy` always includes the OpenVox estate plus this clone's FQDN and
IP so the CA, compilers, and GUI never go through Squid.

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
