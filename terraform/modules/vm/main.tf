# =============================================================================
# ITSYS VMware ISO-based VM Provisioner Module
#
# Creates a virtual machine configured to boot from a datastore-resident
# OS ISO image. Ideal for rapid stand-up of new VMs that will run an
# interactive or kickstart-based OS installation.
#
# After the OS is installed (and usually after a reboot + VMware Tools),
# you can re-apply Terraform (or use other tools) for further customization.
# =============================================================================

locals {
  iso_ds = var.iso_datastore != null ? var.iso_datastore : var.vm_datastore
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
  name          = local.iso_ds
  datacenter_id = data.vsphere_datacenter.dc.id
}

# Resolve all requested networks
data "vsphere_network" "networks" {
  for_each = toset(var.networks)

  name          = each.value
  datacenter_id = data.vsphere_datacenter.dc.id
}

resource "vsphere_virtual_machine" "vm" {
  name             = var.vm_name
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.vm_ds.id

  folder = var.folder != "" ? var.folder : null

  num_cpus                = var.num_cpus
  memory                  = var.memory_mb
  guest_id                = var.guest_id
  firmware                = var.firmware
  efi_secure_boot_enabled = var.efi_secure_boot_enabled

  scsi_type = "pvscsi" # Modern default; change via extra_config or future var if needed

  # Primary + additional network interfaces (in order)
  dynamic "network_interface" {
    for_each = var.networks
    content {
      network_id   = data.vsphere_network.networks[network_interface.value].id
      adapter_type = "vmxnet3"
    }
  }

  # OS / boot disk
  disk {
    label            = "disk0"
    size             = var.disk_size_gb
    thin_provisioned = true
    unit_number      = 0
  }

  # Attach the OS ISO from datastore
  cdrom {
    datastore_id = data.vsphere_datastore.iso_ds.id
    path         = var.iso_path
  }

  # Give the firmware/VM time to see the CD-ROM at power on
  boot_delay = var.boot_delay_ms

  # Important for fresh ISO installs:
  # The guest is not running a full OS with tools yet, so disable waiters
  # or they will time out.
  wait_for_guest_net_timeout = 0
  wait_for_guest_ip_timeout  = 0

  # Optional: expose disk UUIDs to guest
  extra_config = var.enable_disk_uuid ? {
    "disk.EnableUUID" = "TRUE"
  } : {}

  tags = var.tags

  lifecycle {
    # Uncomment for production machines you never want Terraform to destroy
    # prevent_destroy = true
  }
}
