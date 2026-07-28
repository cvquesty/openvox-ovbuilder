# ovbuilder Golden Images (Packer)

Build **versioned vSphere templates** once. Day-to-day `ovbuilder build`
only clones them and injects hostname / IP from the interview.

You can run Packer from **macOS, Linux, or Windows** as long as Packer
and network access to vCenter are available. Guest images themselves are
Linux (AlmaLinux / Ubuntu).

## Secrets

See [../docs/SECRETS.md](../docs/SECRETS.md).

- Copy `variables.auto.pkrvars.hcl.example` → `variables.auto.pkrvars.hcl`
- That file is **gitignored**. Never commit real passwords.

## No-DHCP design (static-IP fleets)

There is **no requirement for DHCP on clone or production guests**. That is
intentional.

| Stage | Network | How identity is set |
|-------|---------|---------------------|
| **Packer golden** | NIC attached; guest often never needs a routable IP | Kickstart / autoinstall; `communicator = "none"`; guest **poweroff** when done |
| **ovbuilder clone** | Interview supplies **unique** hostname, IP, CIDR, gateway, DNS | cloud-init guestinfo at clone time |

Packer does **not** SSH into the golden and does **not** wait for a guest IP.
If a build sits on “Waiting for IP…”, the template is still on an old
SSH/DHCP path — rebuild with the current HCL (`communicator = "none"`).

A **DHCP-backed port group is optional** during Packer only if your
environment requires it for something outside this design. The current
templates are written to finish offline from Packer’s point of view.

| Image key (ovbuilder) | Packer dir | Default template name | Login user |
|-----------------------|------------|------------------------|------------|
| `almalinux-10` | `almalinux-10/` | `ovbuilder-almalinux-10` | `almalinux` |
| `ubuntu-24.04` | `ubuntu-24.04/` | `ovbuilder-ubuntu-24.04` | `ubuntu` |

After a successful clone, the VM should be **ready for SSH login**. Install
the OpenVox agent when firewall/ACLs allow reachability to the server.

AlmaLinux clones also install configured **DNF groups** at first boot
(see the main [README](../README.md#dnf-groups-almalinux--rhel-family)).

## Prerequisites

- [Packer](https://developer.hashicorp.com/packer/install) ≥ 1.9
  (installers for macOS, Linux, and Windows)
- Plugin: `github.com/hashicorp/vsphere` (Packer installs on `init`)
- vCenter credentials with rights to create VMs and convert to template
- ISO for each OS on a datastore **or** reachable HTTP URL (see each
  image’s `*.pkr.hcl`)

## One-time setup

Set variables via environment **or** the gitignored pkrvars file.

### Environment variables (UNIX shells)

```bash
cd packer
export PKR_VAR_vcenter_server='vcenter.example.com'
export PKR_VAR_vcenter_username='administrator@vsphere.local'
export PKR_VAR_vcenter_password='***'
export PKR_VAR_datacenter='Main DC'
export PKR_VAR_cluster='Production Cluster'
export PKR_VAR_datastore='vsanDatastore'
export PKR_VAR_network='VM Network'
export PKR_VAR_folder='Templates'
export PKR_VAR_iso_datastore='isos'
```

### Environment variables (Windows PowerShell)

```powershell
cd packer
$env:PKR_VAR_vcenter_server = 'vcenter.example.com'
$env:PKR_VAR_vcenter_username = 'administrator@vsphere.local'
$env:PKR_VAR_vcenter_password = '***'
$env:PKR_VAR_datacenter = 'Main DC'
$env:PKR_VAR_cluster = 'Production Cluster'
$env:PKR_VAR_datastore = 'vsanDatastore'
$env:PKR_VAR_network = 'VM Network'
$env:PKR_VAR_folder = 'Templates'
$env:PKR_VAR_iso_datastore = 'isos'
```

### pkrvars file (any OS)

```bash
cp variables.auto.pkrvars.hcl.example variables.auto.pkrvars.hcl
# edit credentials and placement
```

PowerShell:

```powershell
Copy-Item variables.auto.pkrvars.hcl.example variables.auto.pkrvars.hcl
```

Then:

```bash
packer init almalinux-10
packer init ubuntu-24.04
```

## Build AlmaLinux 10

```bash
packer build -var-file=variables.auto.pkrvars.hcl almalinux-10
```

Produces a powered-off **template** named `ovbuilder-almalinux-10`
(override with `template_name`).

## Build Ubuntu 24.04

```bash
packer build -var-file=variables.auto.pkrvars.hcl ubuntu-24.04
```

Produces template `ovbuilder-ubuntu-24.04`.

## What is baked into every golden

- Minimal OS (`@core` / equivalent) plus a small tools set
- `open-vm-tools`
- **cloud-init** with VMware guestinfo datasource enabled
- Default admin user; password from gitignored Packer vars
- Cleanup: machine-id, SSH host keys regenerate on first boot, cloud-init clean
- **No** static IP, **no** Puppet/OpenVox signed cert
- **No** full Anaconda “Server” group set — that is applied at **clone time**
  by ovbuilder (`dnf_groups`)

## What ovbuilder injects at clone time

Via `guestinfo.metadata` / `guestinfo.userdata` (cloud-init):

- hostname / FQDN
- static IPv4 address + prefix, gateway, DNS
- guest password (from `OVBUILDER_GOLDEN_PASSWORD` / `secrets.env`)
- EL DNF group install script (Alma only, if `dnf_groups` is non-empty)

## Point ovbuilder at the templates

Config file location depends on OS (see main README). Example:

```yaml
provision_mode: golden

golden_images:
  almalinux-10:
    template: ovbuilder-almalinux-10
    guest_id: other4xLinux64Guest
    default_user: almalinux
  ubuntu-24.04:
    template: ovbuilder-ubuntu-24.04
    guest_id: ubuntu64Guest
    default_user: ubuntu
```

Then:

```bash
ovbuilder build
# Select OS → interview → clone → SSH-ready
```

## Rebuild cadence

Rebuild goldens when:

- Monthly (or after critical CVEs)
- Base ISO major/minor changes
- You change default users, packages, or cloud-init policy

Name new templates with a version suffix if you want side-by-side rollouts
(`ovbuilder-almalinux-10-2026.07.21`) and update config when ready.

## Troubleshooting Packer builds

- **Boot command misses kickstart/autoinstall:** EFI vs BIOS; adjust
  `boot_command` and firmware in the image’s `*.pkr.hcl`.
- **Cannot find ISO:** set ISO path vars to what the vsphere plugin expects.
- **Template already exists:** change `template_name`, or remove/replace the
  old template in vCenter.
- **Waiting for IP:** wrong / outdated template still using SSH communicator.
  Use current HCL with `communicator = "none"`.
