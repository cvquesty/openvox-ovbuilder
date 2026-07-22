# Packer: AlmaLinux 10 → vSphere template (NO DHCP / NO SSH)
#
# Boot strategy (learned the hard way on Alma 10 + dual CD):
#   * Editing the DVD GRUB "linux" line often drops/invalidates stage2 so
#     Anaconda reports missing inst.stage2/inst.repo.
#   * Bare inst.stage2=cdrom is ambiguous with two CDs.
#   * Fix: enter GRUB *command* mode and boot with a full, explicit cmdline:
#       stage2 + repo  = first CD  (/dev/sr0 = AlmaLinux DVD from iso_paths)
#       kickstart      = second CD (/dev/sr1 = OEMDRV seed from cd_content)
#   * Every installer option uses the mandatory "inst." prefix (RHEL/Alma 10).

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

# Golden login password — supply only via gitignored pkrvars / PKR_VAR_ssh_password.
# Never commit real values.
variable "ssh_password" {
  type      = string
  sensitive = true
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

  # CD order: Packer attaches iso_paths first, then generated cd_content ISO.
  # That yields sr0 = Alma DVD, sr1 = kickstart seed (OEMDRV).
  iso_paths = local.iso_paths

  cd_content = {
    "ks.cfg" = templatefile("${path.cwd}/almalinux-10/http/ks.cfg.pkrtpl", {
      ssh_password = var.ssh_password
    })
  }
  cd_label = "OEMDRV"

  boot_wait = "25s"

  # GRUB2 command-line boot (UEFI). Full explicit inst.* parameters.
  # Paths /images/pxeboot/* are the standard Alma/RHEL DVD layout.
  boot_command = [
    "c<wait3>",
    "linuxefi /images/pxeboot/vmlinuz",
    " inst.stage2=hd:/dev/sr0",
    " inst.repo=hd:/dev/sr0",
    " inst.ks=hd:/dev/sr1:/ks.cfg",
    " inst.text",
    "<enter><wait>",
    "initrdefi /images/pxeboot/initrd.img<enter><wait>",
    "boot<enter>",
  ]

  communicator     = "none"
  shutdown_timeout = "90m"

  convert_to_template = true
  remove_cdrom        = true
}

build {
  sources = ["source.vsphere-iso.almalinux10"]
}
