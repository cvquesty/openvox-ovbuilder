# Packer: AlmaLinux 10 → vSphere template (NO DHCP / NO SSH)
#
# Why previous boots hit dracut emergency (kickstart.sh /tmp/ks.cfg.done):
#   Editing GRUB and setting inst.stage2=cdrom / wrong LABEL made dracut wait
#   for stage2 or ks on the wrong device. With TWO CDs, bare "cdrom" is ambiguous.
#
# Reliable approach for Alma/RHEL:
#   1. Leave the DVD's own GRUB entry alone (it already has correct inst.stage2
#      for the install media — with proper inst. prefixes on Alma 10).
#   2. Attach kickstart as second CD labeled OEMDRV with file ks.cfg at root.
#      Anaconda auto-loads ks.cfg from a volume labeled OEMDRV (no inst.ks= needed).
#   3. Only append inst.text if we must edit; prefer default boot (Enter).

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
  default = "ovbuilder-almalinux-10"
}
variable "iso_path" {
  type    = string
  default = "Linux_ISO/AlmaLinux/10/x86_64/AlmaLinux-10.1-x86_64-dvd.iso"
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

source "vsphere-iso" "almalinux10" {
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
  guest_os_type        = "other4xLinux64Guest"
  firmware             = "efi"
  CPUs                 = var.cpu
  RAM                  = var.memory_mb
  disk_controller_type = ["pvscsi"]

  storage {
    disk_size             = var.disk_gb * 1024
    disk_thin_provisioned = true
  }

  # CD1: AlmaLinux DVD (stage2 + packages) — GRUB on this disc is authoritative
  iso_paths = local.iso_paths

  # CD2: kickstart only. Label MUST be OEMDRV for Anaconda auto-load of /ks.cfg
  cd_content = {
    "ks.cfg" = file("${path.cwd}/almalinux-10/http/ks.cfg")
  }
  cd_label = "OEMDRV"

  # Wait for UEFI GRUB from the DVD
  boot_wait = "20s"

  # Do NOT rewrite stage2/repo — DVD defaults are correct on Alma 10.
  # Boot the highlighted GRUB entry (Enter). OEMDRV supplies kickstart automatically.
  # Fallback: if menu needs a key, send Enter twice with waits.
  boot_command = [
    "<enter><wait30s>",
    "<enter><wait>",
  ]

  communicator     = "none"
  shutdown_timeout = "90m"

  convert_to_template = true
  remove_cdrom        = true
}

build {
  sources = ["source.vsphere-iso.almalinux10"]
}
