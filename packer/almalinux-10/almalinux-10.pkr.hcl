# Packer: AlmaLinux 10 → vSphere template (NO DHCP / NO SSH)
#
# Boot media layout (critical):
#   CD1 = AlmaLinux DVD  → inst.stage2 + inst.repo via **DVD volume LABEL**
#   CD2 = OEMDRV seed    → ks.cfg via **LABEL=OEMDRV**
#
# Do NOT use bare "inst.stage2=cdrom" with two CDs — dracut may bind the
# wrong disc and hang in initqueue (or never write /tmp/ks.cfg.done).
#
# AlmaLinux/RHEL 10: every Anaconda cmdline option needs the "inst." prefix.

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
# ISO-9660 Volume id of the AlmaLinux DVD (override if console shows a different LABEL=).
variable "iso_label" {
  type    = string
  default = "AlmaLinux-10-1-x86_64-dvd"
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

  # Install DVD (package repo + stage2)
  iso_paths = local.iso_paths

  # Kickstart seed CD — Anaconda looks for label OEMDRV by design.
  # Keep stage2/repo on the DVD LABEL so this disc is only for ks.cfg.
  cd_content = {
    "ks.cfg" = file("${path.cwd}/almalinux-10/http/ks.cfg")
  }
  cd_label = "OEMDRV"

  boot_wait = "15s"
  #
  # Edit UEFI GRUB linux line: force DVD for stage2/repo, OEMDRV for kickstart.
  # All keys use mandatory "inst." prefix (AlmaLinux / RHEL 10).
  #
  boot_command = [
    "e<wait>",
    "<down><down><end><wait>",
    " inst.stage2=hd:LABEL=${var.iso_label}",
    " inst.repo=hd:LABEL=${var.iso_label}",
    " inst.ks=hd:LABEL=OEMDRV:/ks.cfg",
    " inst.text",
    "<leftCtrlOn>x<leftCtrlOff>",
  ]

  communicator     = "none"
  shutdown_timeout = "90m"

  convert_to_template = true
  remove_cdrom        = true
}

build {
  sources = ["source.vsphere-iso.almalinux10"]
}
