# Packer: Ubuntu 24.04 → vSphere template (NO DHCP / NO SSH)
#
# Media (same dual-CD pattern as Alma):
#   sr0 = Ubuntu live-server ISO (iso_paths)
#   sr1 = NoCloud seed labeled cidata (cd_content: user-data + meta-data)
#
# Offline seed (no Packer HTTP — guest has no DHCP/route to builder).
# Proven vsphere-iso pattern: kernel arg is only `ds=nocloud` (no ;s=path).
# Cloud-init then auto-discovers the volume labeled cidata/CIDATA.
# Explicit seedfrom paths break easily: GRUB eats bare ";", and /cdrom is
# the live ISO not the seed. See HashiCorp Discuss #69422.

packer {
  required_plugins {
    vsphere = {
      source  = "github.com/hashicorp/vsphere"
      version = ">= 1.2.0"
    }
  }
}

variable "vcenter_server" { type = string }
variable "vcenter_username" { type = string }
variable "vcenter_password" {
  type      = string
  sensitive = true
}
variable "datacenter" { type = string }
variable "cluster" { type = string }
variable "datastore" { type = string }
variable "network" { type = string }
variable "folder" {
  type    = string
  default = ""
}
variable "iso_datastore" {
  type    = string
  default = ""
}
variable "insecure_connection" {
  type    = bool
  default = true
}
variable "template_name" {
  type    = string
  default = "ovbuilder-ubuntu-24.04"
}
variable "iso_path" {
  type    = string
  default = "Linux_ISO/Ubuntu/24.04/amd64/ubuntu-24.04-live-server-amd64.iso"
}
variable "cpu" {
  type    = number
  default = 2
}
variable "memory_mb" {
  type    = number
  default = 4096
}
variable "disk_gb" {
  type    = number
  default = 40
}

locals {
  iso_paths = var.iso_datastore != "" ? ["[${var.iso_datastore}] ${var.iso_path}"] : ["[${var.datastore}] ${var.iso_path}"]
}

source "vsphere-iso" "ubuntu2404" {
  vcenter_server      = var.vcenter_server
  username            = var.vcenter_username
  password            = var.vcenter_password
  insecure_connection = var.insecure_connection

  datacenter = var.datacenter
  cluster    = var.cluster
  datastore  = var.datastore
  folder     = var.folder != "" ? var.folder : null

  network_adapters {
    network      = var.network
    network_card = "vmxnet3"
  }

  vm_name              = "${var.template_name}-build"
  guest_os_type        = "ubuntu64Guest"
  firmware             = "efi"
  CPUs                 = var.cpu
  RAM                  = var.memory_mb
  disk_controller_type = ["pvscsi"]

  storage {
    disk_size             = var.disk_gb * 1024
    disk_thin_provisioned = true
  }

  iso_paths = local.iso_paths

  # Prefer SATA CDROM — more reliable for dual-CD discovery on EFI guests.
  cdrom_type = "sata"

  # NoCloud seed on second CD. Filenames must be exactly user-data + meta-data
  # at the ISO root. meta-data may be empty; user-data holds #cloud-config
  # autoinstall: ... (NOT a bare autoinstall.yaml).
  cd_content = {
    "user-data" = file("${path.cwd}/ubuntu-24.04/http/user-data")
    "meta-data" = file("${path.cwd}/ubuntu-24.04/http/meta-data")
  }
  cd_label = "cidata"

  # Catch GRUB before auto-boot (live-server timeout is short).
  boot_wait = "5s"

  # GRUB command mode (same approach as Alma). No seedfrom path — only
  # `ds=nocloud` so cloud-init probes for the cidata-labeled volume.
  # autoinstall goes after --- (installer args), matching working 24.04
  # vsphere-iso reports.
  boot_command = [
    "c<wait3>",
    "linux /casper/vmlinuz --- autoinstall ds=nocloud<enter><wait3>",
    "initrd /casper/initrd<enter><wait3>",
    "boot<enter>",
  ]

  communicator     = "none"
  shutdown_timeout = "120m"

  convert_to_template = true
  remove_cdrom        = true
}

build {
  sources = ["source.vsphere-iso.ubuntu2404"]
}
