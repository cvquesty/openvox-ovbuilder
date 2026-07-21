# ovbuilder Golden Images (Packer)

Build **versioned vSphere templates** once; day-to-day `ovbuilder build` only
clones them and injects hostname / IP from the interview.

## No-DHCP design (PDXC / static-IP fleets)

There is **no DHCP** for build or production guests. That is expected.

| Stage | Network | How identity is set |
|-------|---------|---------------------|
| **Packer golden** | NIC attached, **no IP required** | Kickstart/autoinstall only; `communicator = "none"`; guest **poweroff** when done |
| **ovbuilder clone** | Interview supplies **unique** hostname, IP, CIDR, gateway, DNS | cloud-init guestinfo at clone time |

Packer never SSHes into the golden and never waits for a guest IP. If a build
sits on “Waiting for IP…”, the template is still on the old SSH/DHCP path —
rebuild with the current HCL (`communicator = "none"`).

| Image key (ovbuilder) | Packer dir | Default template name | Login user |
|----------------------|------------|------------------------|------------|
| `almalinux-10` | `almalinux-10/` | `ovbuilder-almalinux-10` | `almalinux` |
| `ubuntu-24.04` | `ubuntu-24.04/` | `ovbuilder-ubuntu-24.04` | `ubuntu` |

After a successful clone, the VM should be **ready for SSH login**. Install the
OpenVox agent when firewall/ACLs allow reachability to the server
(`curl …/packages/install.bash | sudo bash`).

## Prerequisites

- [Packer](https://developer.hashicorp.com/packer/install) ≥ 1.9
- Plugin: `github.com/hashicorp/vsphere` (Packer will install on `init`)
- vCenter credentials with rights to create VMs and convert to template
- ISO for each OS on a datastore **or** reachable HTTP URL (see each image’s `*.pkr.hcl`)
- A DHCP-backed port group for the **build** network (install-time only)

## One-time setup

```bash
cd packer
export PKR_VAR_vcenter_server='vcenter.example.com'
export PKR_VAR_vcenter_username='administrator@vsphere.local'
export PKR_VAR_vcenter_password='***'
export PKR_VAR_datacenter='Main DC'
export PKR_VAR_cluster='Production Cluster'
export PKR_VAR_datastore='vsanDatastore'
export PKR_VAR_network='VM Network'          # DHCP during Packer build
export PKR_VAR_folder='Templates'            # optional
export PKR_VAR_iso_datastore='isos'          # if using datastore ISO paths

# Optional: override ISO paths on the datastore
# export PKR_VAR_iso_path='isos/AlmaLinux-10.0-x86_64-dvd.iso'

packer init almalinux-10
packer init ubuntu-24.04
```

Copy `variables.auto.pkrvars.hcl.example` → `variables.auto.pkrvars.hcl` if you
prefer files over env vars (do **not** commit secrets).

## Build AlmaLinux 10

```bash
packer build -var-file=variables.auto.pkrvars.hcl almalinux-10
```

Produces a powered-off **template** named `ovbuilder-almalinux-10` (override with
`template_name`).

## Build Ubuntu 24.04

```bash
packer build -var-file=variables.auto.pkrvars.hcl ubuntu-24.04
```

Produces template `ovbuilder-ubuntu-24.04`.

## What is baked into every golden

- Minimal OS + updates (best-effort during build)
- `open-vm-tools` / `open-vm-tools-desktop` stack as appropriate
- **cloud-init** with VMware guestinfo datasource enabled
- Default admin user with password + optional SSH key (set via Packer vars)
- Cleanup: machine-id, SSH host keys regenerate on first boot, cloud-init clean
- **No** static IP, **no** Puppet/OpenVox signed cert

## What ovbuilder injects at clone time

Via `guestinfo.metadata` / `guestinfo.userdata` (cloud-init):

- hostname / FQDN
- static IPv4 address + prefix, gateway, DNS

## Point ovbuilder at the templates

`~/.config/ovbuilder/config.yaml`:

```yaml
provision_mode: golden   # default after this release; use "iso" for legacy

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
# Select OS → interview (hostname, IP, CIDR, …) → clone → SSH-ready
```

## Rebuild cadence

Rebuild goldens when:

- Monthly (or after critical CVEs)
- Base ISO major/minor changes
- You change default users, packages, or cloud-init policy

Name new templates with a version suffix if you want side-by-side rollouts
(`ovbuilder-almalinux-10-2026.07.21`) and update `config.yaml` when ready.

## Troubleshooting Packer builds

- **Boot command misses kickstart/autoinstall:** EFI vs BIOS; adjust `boot_command` and firmware in the image’s `*.pkr.hcl`.
- **Cannot find ISO:** set `iso_paths` to the datastore path Packer expects (`[datastore] path/to.iso` style is handled by the vsphere plugin vars).
- **Template already exists:** Packer destroy/replace, or change `template_name`.
