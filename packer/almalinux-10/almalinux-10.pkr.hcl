# Packer: AlmaLinux 10 → vSphere template (NO DHCP / NO SSH)
#
# Dual-CD pitfall (root cause of dracut-initqueue timeouts):
#   If kickstart is a second virtual CD (cd_content), Anaconda/dracut may treat
#   that tiny seed ISO as "cdrom" and never find stage2 on the Alma DVD.
#   Fix: deliver ks.cfg on a **floppy** so the only CD is the real install media.
#
# AlmaLinux / RHEL 10: all installer cmdline options need the "inst." prefix.

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
# Volume label printed on the AlmaLinux DVD (isoinfo -d -i *.iso → Volume id).
# Override if a newer ISO renames the label.
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

  # ONLY the AlmaLinux install DVD as a CD-ROM
  iso_paths = local.iso_paths

  # Kickstart on floppy (avoids second CD stealing "cdrom" / stage2)
  floppy_content = {
    "ks.cfg" = file("${path.cwd}/almalinux-10/http/ks.cfg")
  }

  boot_wait = "15s"
  #
  # UEFI GRUB on Alma 10 DVD: edit the default linux line and boot (Ctrl-x).
  # Explicit LABEL for stage2/repo so dracut does not hunt the wrong device.
  # Floppy kickstart: hd:fd0 is the virtual floppy Packer attaches.
  #
  boot_command = [
    "e<wait>",
    "<down><down><end><wait>",
    " inst.stage2=hd:LABEL=${var.iso_label}",
    " inst.repo=hd:LABEL=${var.iso_label}",
    " inst.ks=hd:fd0:/ks.cfg",
    " inst.text",
    " inst.sshd",
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
