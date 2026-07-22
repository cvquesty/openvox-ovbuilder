# Secrets — never commit

ovbuilder and Packer **must not** store real passwords, API tokens, or
vCenter credentials in the git repo. GitHub secret scanning will flag them
and they become an incident.

## What lives where

| Secret | Where (local only) | Used by |
|--------|--------------------|---------|
| Golden / clone login password | `~/.config/ovbuilder/secrets.env` or `OVBUILDER_GOLDEN_PASSWORD` | `ovbuilder build` (cloud-init guestinfo) |
| Packer vCenter + golden password | `packer/variables.auto.pkrvars.hcl` (**gitignored**) | `packer build` |
| vSphere password for ovbuilder | CLI prompt, `--vsphere-password`, or `VSPHERE_PASSWORD` / `TF_VAR_vsphere_password` | Terraform |

## Golden password (clones)

```bash
mkdir -p ~/.config/ovbuilder
chmod 700 ~/.config/ovbuilder
cat > ~/.config/ovbuilder/secrets.env <<'EOF'
# Lab only — rotate if ever leaked. Never commit this file.
OVBUILDER_GOLDEN_PASSWORD='choose-a-strong-lab-password'
EOF
chmod 600 ~/.config/ovbuilder/secrets.env
```

Or one-shot:

```bash
export OVBUILDER_GOLDEN_PASSWORD='choose-a-strong-lab-password'
ovbuilder build
```

If unset, golden clone will **fail** with instructions instead of baking a
default password into the repo.

## Packer golden builds

```bash
cd packer
cp variables.auto.pkrvars.hcl.example variables.auto.pkrvars.hcl
chmod 600 variables.auto.pkrvars.hcl
# Edit: vcenter_* and ssh_password / ssh_password_crypted
# ssh_password_crypted:  openssl passwd -6 'same-as-ssh_password'
```

`*.pkrvars.hcl` is gitignored. Only `*.pkrvars.hcl.example` is tracked.

## If a secret was already pushed

1. **Rotate** it immediately (vCenter password, lab golden password, etc.).
2. Remove it from the tree (this doc’s process).
3. Optionally purge git history (`git filter-repo`) and force-push — only with
   team agreement; still treat the secret as burned.

## Checklist before every push

```bash
git status
git grep -iE 'password\s*=\s*"|passwd|BEGIN (RSA |OPENSSH )?PRIVATE' -- ':!*.example' ':!docs/SECRETS.md' || true
```

No real passwords, no private keys, no production tokens.
