# modules/vm — ISO-based VMware VM Provisioner

Reusable Terraform module to rapidly provision new virtual machines in vSphere by booting directly from a datastore-resident OS ISO image.

## Why this module?

- Designed for speed: stand up a new VM in one `terraform apply`.
- Uses **datastore ISOs** (no templates required).
- Works from a laptop (Mac) or any Linux host that has Terraform.
- After the OS installer finishes (kickstart or manual), the VM is ready for further configuration (Puppet, Ansible, etc.).

## Usage

### Minimal example (root module)

```hcl
module "new_vm" {
  source = "./modules/vm"

  vm_name       = "itsys-test01"
  datacenter    = "SMFC DC"
  cluster       = "Compute Cluster"
  vm_datastore  = "vsanDatastore"
  iso_datastore = "isos"
  iso_path      = "isos/AlmaLinux-9.4-x86_64-dvd.iso"

  num_cpus    = 4
  memory_mb   = 8192
  disk_size_gb = 120

  networks = ["VM Production", "VM iSCSI"]
}
```

### Full variable-driven example (recommended)

See the root `main.tf` + `terraform.tfvars` for a complete pattern.

## Important Notes for ISO Boots

1. **No guest customization during create** — the VM is booting an installer, not a finished guest.
2. Set `wait_for_guest_*` to 0 (module does this by default).
3. Use `boot_delay_ms` (default 10s) so the BIOS/UEFI sees the CD.
4. After OS install + first boot + VMware Tools install, IPs will start appearing.
5. You can safely re-run `terraform apply` later to adjust CPU/RAM/disk or add NICs.

## Common guest_id values

- RHEL / Alma / Rocky / CentOS 9: `rhel9_64Guest`
- Ubuntu 22.04/24.04: `ubuntu64Guest` or `ubuntu22_64Guest`
- Use PowerCLI on the vCenter to list all valid IDs for your environment:

```powershell
Connect-VIServer vc01.smfc-it.twitter.biz
$esxi = Get-VMHost | Select -First 1
$envBrowser = Get-View $esxi.ExtensionData.Parent.ExtensionData.ConfigManager.EnvironmentBrowser
$envBrowser.QueryConfigOptionDescriptor() | % { $_.Key }
```

## Recommended Workflow

1. `terraform apply` → VM powers on with ISO attached.
2. Connect to the VM console in vSphere Client (or use a kickstart-enabled ISO).
3. Complete OS install.
4. (Optional) Re-run Terraform to grow disk/CPU or attach more networks.
5. Run your configuration management (Puppet/OpenVox, Ansible...).

## Inputs

See `variables.tf` for all options and defaults.

## Outputs

- `vm_id`
- `vm_name`
- `default_ip_address`
- `iso_attached`

## Limitations / Future Work

- Currently creates a single primary disk.
- No built-in kickstart parameter injection (put that in your ISO or use extra_config).
- For very advanced needs (multiple disks at creation time, advanced SCSI bus, etc.), extend the module or open an issue.

Pull requests welcome.
