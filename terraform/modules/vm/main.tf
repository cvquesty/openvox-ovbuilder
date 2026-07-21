# =============================================================================
# ovbuilder VM module — dual mode:
#   provision_mode = "clone"  → clone Packer golden template + cloud-init identity
#   provision_mode = "iso"    → legacy empty disk + ISO attach
# =============================================================================

locals {
  iso_ds       = var.iso_datastore != null && var.iso_datastore != "" ? var.iso_datastore : var.vm_datastore
  is_clone     = var.provision_mode == "clone"
  is_iso       = var.provision_mode == "iso"
  guestinfo    = var.guestinfo_extra_config
}

data "vsphere_datacenter" "dc" {
  name = var.datacenter
}

data "vsphere_compute_cluster" "cluster" {
  name          = var.cluster
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_datastore" "vm_ds" {
  name          = var.vm_datastore
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_datastore" "iso_ds" {
  count         = local.is_iso ? 1 : 0
  name          = local.iso_ds
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "networks" {
  for_each = toset(var.networks)

  name          = each.value
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_virtual_machine" "template" {
  count         = local.is_clone ? 1 : 0
  name          = var.template_name
  datacenter_id = data.vsphere_datacenter.dc.id
}

# ─── Golden clone path ───────────────────────────────────────────────────────
resource "vsphere_virtual_machine" "from_template" {
  count = local.is_clone ? 1 : 0

  name             = var.vm_name
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.vm_ds.id
  folder           = var.folder != "" ? var.folder : null

  num_cpus                = var.num_cpus
  memory                  = var.memory_mb
  guest_id                = var.guest_id != "" ? var.guest_id : data.vsphere_virtual_machine.template[0].guest_id
  firmware                = var.firmware
  efi_secure_boot_enabled = var.efi_secure_boot_enabled
  scsi_type               = data.vsphere_virtual_machine.template[0].scsi_type

  dynamic "network_interface" {
    for_each = var.networks
    content {
      network_id   = data.vsphere_network.networks[network_interface.value].id
      adapter_type = "vmxnet3"
    }
  }

  disk {
    label            = "disk0"
    size             = max(var.disk_size_gb, data.vsphere_virtual_machine.template[0].disks.0.size)
    thin_provisioned = true
    unit_number      = 0
  }

  clone {
    template_uuid = data.vsphere_virtual_machine.template[0].id
  }

  # Tools + OS exist — allow short guest wait (0 = don't fail the apply)
  wait_for_guest_net_timeout = var.wait_for_guest_net_timeout
  wait_for_guest_ip_timeout  = var.wait_for_guest_ip_timeout

  extra_config = merge(
    var.enable_disk_uuid ? { "disk.EnableUUID" = "TRUE" } : {},
    local.guestinfo,
  )

  tags = var.tags
}

# ─── Legacy ISO path ─────────────────────────────────────────────────────────
resource "vsphere_virtual_machine" "from_iso" {
  count = local.is_iso ? 1 : 0

  name             = var.vm_name
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.vm_ds.id
  folder           = var.folder != "" ? var.folder : null

  num_cpus                = var.num_cpus
  memory                  = var.memory_mb
  guest_id                = var.guest_id != "" ? var.guest_id : "otherLinux64Guest"
  firmware                = var.firmware
  efi_secure_boot_enabled = var.efi_secure_boot_enabled
  scsi_type               = "pvscsi"

  dynamic "network_interface" {
    for_each = var.networks
    content {
      network_id   = data.vsphere_network.networks[network_interface.value].id
      adapter_type = "vmxnet3"
    }
  }

  disk {
    label            = "disk0"
    size             = var.disk_size_gb
    thin_provisioned = true
    unit_number      = 0
  }

  cdrom {
    datastore_id = data.vsphere_datastore.iso_ds[0].id
    path         = var.iso_path
  }

  boot_delay                 = var.boot_delay_ms
  wait_for_guest_net_timeout = 0
  wait_for_guest_ip_timeout  = 0

  extra_config = merge(
    var.enable_disk_uuid ? { "disk.EnableUUID" = "TRUE" } : {},
    { "bios.bootDeviceClasses" = "allow:cd,hd,net" },
  )

  tags = var.tags
}
