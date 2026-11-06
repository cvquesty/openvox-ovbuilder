# Terraform state and parallel builds

ovbuilder runs one Terraform apply per VM. Each hostname gets its own state,
its own working copy of the module, and an exclusive lock, so several builds
(CLI or web workers) can run at once without touching each other.

## Layout

Everything for one VM lives under the data directory
(`$XDG_DATA_HOME/ovbuilder`, usually `~/.local/share/ovbuilder`):

```text
tfstate/<hostname>/
  .ovbuilder.lock              exclusive lock held for init + apply
  module/                      private copy of the Terraform root module
  .terraform/                  TF_DATA_DIR (providers, backend metadata)
  ovbuilder.auto.tfvars.json   build vars, mode 0600
  terraform.tfstate            state (local backend only)
  backend.hcl                  backend settings, mode 0600 (remote only)
```

The directory is created with mode 0700. `module/` is refreshed from the
installed module (`terraform_dir`) on every build, so edits to the installed
module take effect on the next build. Never edit `module/` itself.

## Locking

- **Same hostname, two builds:** the second fails right away with
  `Another build is already running for <hostname>`. The OS drops the lock
  when the process exits, even after a crash, so there is no stale lock to
  clean up.
- **Terraform state lock:** apply runs with `-lock=true` and
  `-lock-timeout=<terraform_lock_timeout>` (default `60s`). This protects the
  state against anything outside ovbuilder, including remote-backend users on
  other hosts.

The web workers call `ovbuilder build`, so they get the same isolation. All
workers on a host must share one data directory. The worker sets
`XDG_DATA_HOME=$OVBUILDER_HOME/.local/share`, so keep `OVBUILDER_HOME` the
same for every worker. With the local backend, workers on **different** hosts
do not see each other's state; use a remote backend for multi-host workers.

## Remote backends

Set `terraform_backend` in `config.yaml` (or `OVBUILDER_TERRAFORM_BACKEND`).
ovbuilder writes a backend override into the VM's working copy and passes the
settings through `backend.hcl`, so nothing appears on the command line. Each
VM is stored under `<terraform_state_prefix>/<hostname>`:

| Backend | Per-VM setting | Value |
|---------|----------------|-------|
| `local` (default) | `path` | `tfstate/<hostname>/terraform.tfstate` |
| `s3` | `key` | `<prefix>/<hostname>/terraform.tfstate` |
| `azurerm` | `key` | `<prefix>/<hostname>/terraform.tfstate` |
| `gcs` | `prefix` | `<prefix>/<hostname>` |
| `consul` | `path` | `<prefix>/<hostname>` |

Example (S3 with native locking, Terraform 1.10+):

```yaml
terraform_backend: s3
terraform_state_prefix: ovbuilder/prod
terraform_backend_config:
  bucket: example-tf-state
  region: us-east-1
  encrypt: true
  use_lockfile: true      # or dynamodb_table: example-tf-locks
terraform_lock_timeout: 5m
```

Put only non-secret settings in `terraform_backend_config`. Give credentials
through the backend's own environment variables (`AWS_*` or an instance
profile, `ARM_*`, `GOOGLE_APPLICATION_CREDENTIALS`, `CONSUL_HTTP_TOKEN`) in
the CLI environment or the web worker's systemd unit.

### Moving from local to remote state

When a remote backend is configured and a VM still has a local
`terraform.tfstate`, the next build of that VM runs `terraform state push`
once, then renames the local file to `terraform.tfstate.migrated`. The push
runs without `-force`, so Terraform refuses it if the remote already holds a
different state for that VM; the build then stops and leaves the local file in
place for you to review.

## Rules for operators

- Do not run bare `terraform apply` in the installed module directory. It has
  no per-VM backend path and would create a shared state that renames VMs.
- To inspect one VM's state, work in its copy with its data dir:

  ```bash
  cd ~/.local/share/ovbuilder/tfstate/<hostname>/module
  TF_DATA_DIR=../.terraform terraform state list
  ```

- Older releases kept a single `terraform.tfstate` in the module directory.
  It is moved into `tfstate/<hostname>/` on the first apply.
