# =============================================================================
# ovbuilder VM module — dual mode
# =============================================================================
#
# provision_mode = "clone"
#   Clone a Packer golden template. Identity (hostname/IP) is injected via
#   guestinfo_* extra_config (cloud-init). No install ISO is attached.
#   Disk size is max(requested, template disk) so clones never shrink.
#   The golden may live in a different datacenter of the same vCenter
#   (template_datacenter); destination cluster/host, datastore, and
#   networks stay on var.datacenter.
#
# provision_mode = "iso"
#   Create empty thin disk + attach datastore ISO for interactive/kickstart
#   install. CD-ROM is intentionally left attached at create time so the
#   guest can boot the media. ovbuilder disconnects the ISO via pyVmomi after
#   OS install; lifecycle.ignore_changes on cdrom prevents re-attach on apply.
#
# compute_type = "cluster" (default)
#   Place via data.vsphere_compute_cluster (ATLC / PDXC DRS clusters).
# compute_type = "host"
#   Place via data.vsphere_host resource pool (standalone ESXi, e.g. SEA3).
#
# Both VM resources use count so only one exists in state. Per-VM state files
# (outside this module) ensure ovca3 create does not rename ovca2.
# =============================================================================

locals {
  # Prefer dedicated ISO datastore; fall back to VM datastore when unset.
  iso_ds   = var.iso_datastore != null && var.iso_datastore != "" ? var.iso_datastore : var.vm_datastore
  is_clone = var.provision_mode == "clone"
  is_iso   = var.provision_mode == "iso"
  use_dsc  = var.vm_datastore_cluster != null && var.vm_datastore_cluster != ""
  is_host  = var.compute_type == "host"
  # Golden may live in another DC of the same vCenter (SEA3 clones PDXC goldens).
  template_dc_name = var.template_datacenter != "" ? var.template_datacenter : var.datacenter
  template_dc_is_remote = local.is_clone && local.template_dc_name != var.datacenter
  # Map of guestinfo.* keys from ovbuilder (base64 metadata/userdata).
  guestinfo = var.guestinfo_extra_config
}

data "vsphere_datacenter" "dc" {
  name = var.datacenter
}

data "vsphere_datacenter" "template_dc" {
  count = local.template_dc_is_remote ? 1 : 0
  name  = local.template_dc_name
}

data "vsphere_compute_cluster" "cluster" {
  count         = local.is_host ? 0 : 1
  name          = var.cluster
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_host" "host" {
  count         = local.is_host ? 1 : 0
  name          = var.cluster
  datacenter_id = data.vsphere_datacenter.dc.id
}

locals {
  resource_pool_id = local.is_host ? data.vsphere_host.host[0].resource_pool_id : data.vsphere_compute_cluster.cluster[0].resource_pool_id
  host_system_id   = local.is_host ? data.vsphere_host.host[0].id : null
}

data "vsphere_datastore_cluster" "vm_dsc" {
  count         = local.use_dsc ? 1 : 0
  name          = var.vm_datastore_cluster
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_datastore" "vm_ds" {
  count         = local.use_dsc ? 0 : 1
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
  datacenter_id = local.template_dc_is_remote ? data.vsphere_datacenter.template_dc[0].id : data.vsphere_datacenter.dc.id
}

# ─── Golden clone path ───────────────────────────────────────────────────────
resource "vsphere_virtual_machine" "from_template" {
  count = local.is_clone ? 1 : 0

  name                 = var.vm_name
  resource_pool_id     = local.resource_pool_id
  host_system_id       = local.host_system_id
  datastore_id         = local.use_dsc ? null : data.vsphere_datastore.vm_ds[0].id
  datastore_cluster_id = local.use_dsc ? data.vsphere_datastore_cluster.vm_dsc[0].id : null
  folder               = var.folder != "" ? var.folder : null

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

  name                 = var.vm_name
  resource_pool_id     = local.resource_pool_id
  host_system_id       = local.host_system_id
  datastore_id         = local.use_dsc ? null : data.vsphere_datastore.vm_ds[0].id
  datastore_cluster_id = local.use_dsc ? data.vsphere_datastore_cluster.vm_dsc[0].id : null
  folder               = var.folder != "" ? var.folder : null

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

  # Attach install ISO for first boot only. ovbuilder disconnects the media
  # via pyVmomi after the OS is installed (disconnect_install_media). We
  # ignore_changes on cdrom so a later terraform apply does not re-lock the ISO.
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

  lifecycle {
    ignore_changes = [
      cdrom,
    ]
  }
}
