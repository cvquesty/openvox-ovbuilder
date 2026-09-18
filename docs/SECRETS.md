# Secrets — never commit

ovbuilder and Packer **must not** store real passwords, API tokens, or
vCenter credentials in the git repository. If they land in git, treat them
as burned: rotate immediately.

This guide covers **macOS, Linux, and Windows**.

## What lives where

| Secret | Local only (not in git) | Used by |
|--------|-------------------------|---------|
| Golden / clone login password | `secrets.env` or `OVBUILDER_GOLDEN_PASSWORD` | Only if `OVBUILDER_ALLOW_PASSWORD_SSH` is set |
| Clone SSH public keys | `authorized_keys` file or `OVBUILDER_SSH_AUTHORIZED_KEYS` | `ovbuilder build` (cloud-init guestinfo) |
| HTTP/HTTPS proxy (with auth) | `secrets.env` `OVBUILDER_HTTP_PROXY` | Clone-time apt/dnf/profile.d |
| Packer vCenter + golden password | `packer/variables.auto.pkrvars.hcl` (gitignored) | `packer build` |
| vSphere password for ovbuilder | CLI prompt, env (`VSPHERE_PASSWORD` / `OVBUILDER_VSPHERE_PASSWORD` / `TF_VAR_vsphere_password`), or `secrets.env`. Avoid `--vsphere-password` (visible in `ps`) | Terraform + web worker |
| vSphere password for the web app | `/opt/ovbuilder/web/backend/.env` (`VSPHERE_PASSWORD` or `OVBUILDER_VSPHERE_PASSWORD`) or the admin Configuration page | Live inventory + VM lifecycle |
| vSphere password on a submitted build | Postgres `build_jobs.request` JSON (in-flight jobs). Never in Celery/Redis task args or API responses | Celery worker |
| Web API JWT secret | `web/backend/.env` `SECRET_KEY` (required unless `DEBUG=true`) | FastAPI |
| LDAP TLS CA | `LDAP_CA_CERTS_FILE` PEM path | Web LDAP bind |

### Config directory (where `secrets.env` goes)

| Platform | Default directory |
|----------|-------------------|
| Linux / macOS | `~/.config/ovbuilder/` |
| Windows | `%APPDATA%\ovbuilder\` (usually `C:\Users\<you>\AppData\Roaming\ovbuilder\`) |
| Override (any OS) | `$XDG_CONFIG_HOME/ovbuilder/` |

File name: `secrets.env`

## Clone SSH access (preferred: keys)

Clone-time cloud-init defaults to **key-based SSH**: `ssh_pwauth: false`,
`disable_root: true`, and **no** `chpasswd` (root stays locked). Put your
public keys in the config directory:

```bash
mkdir -p ~/.config/ovbuilder
chmod 700 ~/.config/ovbuilder
# One OpenSSH public key per line. Never put a private key here.
cat ~/.ssh/id_ed25519.pub >> ~/.config/ovbuilder/authorized_keys
chmod 600 ~/.config/ovbuilder/authorized_keys
```

Or for one shell session:

```bash
export OVBUILDER_SSH_AUTHORIZED_KEYS="$(cat ~/.ssh/id_ed25519.pub)"
```

Override the file path with `OVBUILDER_SSH_AUTHORIZED_KEYS_FILE`.

## Golden password (opt-in password SSH)

Password SSH on clones is **off by default**. Set
`OVBUILDER_ALLOW_PASSWORD_SSH=1` only for a documented bootstrap window.
When opted in, cloud-init may set the **default user** password from
`OVBUILDER_GOLDEN_PASSWORD` / `secrets.env` and enable `ssh_pwauth`.
Root is never unlocked and is never listed in `chpasswd`.

That password must come from **your machine**, not from the repository.

### macOS / Linux / WSL / Git Bash

```bash
mkdir -p ~/.config/ovbuilder
chmod 700 ~/.config/ovbuilder
printf "%s\n" "OVBUILDER_ALLOW_PASSWORD_SSH=1" "OVBUILDER_GOLDEN_PASSWORD=" \
  > ~/.config/ovbuilder/secrets.env
chmod 600 ~/.config/ovbuilder/secrets.env
# Edit the file and put your local lab password after the equals.
# Never commit secrets.env.
```

Or for one shell session only (set the value in your shell, not in git):

```bash
export OVBUILDER_ALLOW_PASSWORD_SSH=1
export OVBUILDER_GOLDEN_PASSWORD=
# then assign your local lab password in that same shell
ovbuilder build
```

### Windows PowerShell

```powershell
$dir = Join-Path $env:APPDATA "ovbuilder"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Set-Content -Path (Join-Path $dir "secrets.env") -Value @(
  "OVBUILDER_ALLOW_PASSWORD_SSH=1",
  "OVBUILDER_GOLDEN_PASSWORD="
)
# Edit the file and put your local lab password after the equals.
```

Or for one PowerShell session only (set the value in your session, not in git):

```powershell
$env:OVBUILDER_ALLOW_PASSWORD_SSH = '1'
$env:OVBUILDER_GOLDEN_PASSWORD = ''
# then assign your local lab password in that same session
ovbuilder build
```

### Optional YAML form

Same directory, file `secrets.yaml`. Create the file locally and set
`golden_password` there. Do not put a real value in git.

If password SSH is opted in and the password is unset, golden clone
**stops early** with setup instructions instead of using a hard-coded
default.

## HTTP proxy (clone time)

Do **not** put the proxy password in git or Packer seeds. Add it to the
same `secrets.env`:

```bash
printf "%s\n" "OVBUILDER_HTTP_PROXY=" >> ~/.config/ovbuilder/secrets.env
# Edit secrets.env and set http://user:…@httpproxy.example.com:3128 locally.
# Never commit that file.
chmod 600 ~/.config/ovbuilder/secrets.env
```

cloud-init writes `/etc/profile.d/ovbuilder-proxy.sh`, `/etc/environment`,
apt `01ovbuilder-proxy`, and dnf `dnf.conf.d/ovbuilder-proxy.conf`.
`no_proxy` always includes the OpenVox estate plus this clone's FQDN and
IP so the CA, compilers, and GUI never go through Squid.

## Packer golden builds

Packer needs vCenter credentials and the guest password for the golden
admin user (console login). Templates do **not** bake a well-known default
password; they substitute `ssh_password` / `ssh_password_crypted` from this
local file. Root is locked on the golden. The admin user (`ubuntu` /
`almalinux`) uses password-required sudo. Clone-time SSH prefers keys;
that Packer password is only re-applied at clone time if
`OVBUILDER_ALLOW_PASSWORD_SSH` is set.

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
openssl passwd -6
# type the same local lab password when prompted
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
