# modules/vm — vSphere VM provisioner (clone or ISO)

Reusable Terraform module used by ovbuilder to create VMs in vSphere.

Two modes:

| `provision_mode` | What happens |
|------------------|--------------|
| `clone` | Clone a Packer golden template; inject cloud-init `guestinfo_*` |
| `iso` | Empty thin disk + attach a datastore ISO for console install |

Most people should run **`ovbuilder build`** instead of calling this module
directly. This README is for operators who wire Terraform themselves.

## Why this module?

- One `terraform apply` creates or updates a single VM.
- Works from macOS, Linux, or Windows as long as Terraform and the
  HashiCorp vSphere provider can reach vCenter.
- Thin-provisioned disks; guest waiters disabled for ISO boots.
- Per-VM state is handled by the ovbuilder CLI (not by this module alone).

## Usage

### Clone (golden)

```hcl
module "new_vm" {
  source = "./modules/vm"

  provision_mode = "clone"
  vm_name        = "openvox-web03"
  template_name  = "ovbuilder-almalinux-10"
  datacenter     = "Main DC"
  cluster        = "Compute Cluster"
  vm_datastore   = "vsanDatastore"
  networks       = ["VM Production"]

  num_cpus     = 4
  memory_mb    = 8192
  disk_size_gb = 120

  guestinfo_extra_config = {
    # produced by ovbuilder.cloud_init.guestinfo_extra_config
  }
}
```

### ISO (legacy)

```hcl
module "new_vm" {
  source = "./modules/vm"

  provision_mode = "iso"
  vm_name        = "itsys-test01"
  datacenter     = "Main DC"
  cluster        = "Compute Cluster"
  vm_datastore   = "vsanDatastore"
  iso_datastore  = "isos"
  iso_path       = "isos/AlmaLinux-10.0-x86_64-dvd.iso"

  num_cpus     = 4
  memory_mb    = 8192
  disk_size_gb = 120

  networks = ["VM Production"]
}
```

Root module wiring lives in `terraform/main.tf` and `terraform/variables.tf`.

## ISO boot notes

1. No guest customization during create — the VM is booting an installer.
2. `wait_for_guest_*` is 0 (module default).
3. `boot_delay_ms` (default 10s) helps firmware see the CD.
4. After OS install, disconnect the ISO (ovbuilder does this) before reboot
   so the datastore file is not locked.
5. You can re-run `terraform apply` later to change CPU/RAM/disk or NICs.

## Common guest_id values

- Alma / RHEL 9–10 style: `rhel9_64Guest` or `other4xLinux64Guest`
- Ubuntu 22.04 / 24.04: `ubuntu64Guest`

List valid IDs with PowerCLI (Windows or PowerShell Core on UNIX):

```powershell
Connect-VIServer vcenter.example.com
$esxi = Get-VMHost | Select-Object -First 1
$envBrowser = Get-View $esxi.ExtensionData.Parent.ExtensionData.ConfigManager.EnvironmentBrowser
$envBrowser.QueryConfigOptionDescriptor() | ForEach-Object { $_.Key }
```

## Recommended workflows

### Golden (preferred)

1. Build templates with Packer (`packer/README.md`).
2. `ovbuilder build` (or Terraform clone mode).
3. Guest boots; cloud-init applies identity.
4. Optional OpenVox agent install.

### ISO

1. `terraform apply` / `ovbuilder build --mode iso`.
2. Complete OS install in the vSphere console.
3. Disconnect ISO, reboot into installed OS.
4. Configure network / agent (ovbuilder SSH helpers or manual).

## Inputs and outputs

See `variables.tf` and `outputs.tf`.

Notable outputs: `vm_id`, `vm_name`, `default_ip_address`, `iso_attached`,
`provision_mode`.

## Limitations

- Single primary disk at create time.
- Kickstart content is not injected here (Packer goldens or ISO content).
- Prefer ovbuilder for per-VM state isolation; a shared root state will
  treat hostname changes as in-place renames.
