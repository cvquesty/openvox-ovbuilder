# Packer: AlmaLinux 10 → vSphere template for ovbuilder
#
# Kickstart is delivered via a second virtual CD (cd_content), NOT Packer's
# HTTP server — the build laptop is not reachable from PDXC guest networks.

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
variable "ssh_password" {
  type      = string
  default   = "ChangeMe-BuildOnly!"
  sensitive = true
}
variable "ssh_public_key" {
  type    = string
  default = ""
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

  iso_paths = local.iso_paths

  # Embed kickstart on a second ISO so the guest never needs HTTP to the laptop.
  # Use path.cwd (packer/ when invoked as `packer build almalinux-10` from packer/).
  cd_content = {
    "ks.cfg" = file("${path.cwd}/almalinux-10/http/ks.cfg")
  }
  cd_label = "OEMDRV"

  boot_wait = "8s"
  # UEFI GRUB: append kickstart from the OEMDRV / cdrom label (RHEL finds OEMDRV automatically too)
  boot_command = [
    "e<down><down><end> inst.ks=cdrom:/dev/sr1:/ks.cfg inst.sshd<leftCtrlOn>x<leftCtrlOff>"
  ]

  ssh_username           = "almalinux"
  ssh_password           = var.ssh_password
  ssh_timeout            = "90m"
  ssh_handshake_attempts = 200

  shutdown_command    = "echo '${var.ssh_password}' | sudo -S /sbin/shutdown -h now"
  convert_to_template = true
  remove_cdrom        = true
}

build {
  sources = ["source.vsphere-iso.almalinux10"]

  provisioner "shell" {
    execute_command = "echo '${var.ssh_password}' | {{ .Vars }} sudo -S -E bash '{{ .Path }}'"
    scripts = [
      "${path.root}/../scripts/install-alma.sh",
      "${path.root}/../scripts/cleanup-linux.sh",
    ]
  }

  provisioner "shell" {
    execute_command = "echo '${var.ssh_password}' | {{ .Vars }} sudo -S -E bash -c '{{ .Vars }} {{ .Path }}'"
    inline = [
      "if [ -n '${var.ssh_public_key}' ]; then mkdir -p /home/almalinux/.ssh && echo '${var.ssh_public_key}' >> /home/almalinux/.ssh/authorized_keys && chown -R almalinux:almalinux /home/almalinux/.ssh && chmod 700 /home/almalinux/.ssh && chmod 600 /home/almalinux/.ssh/authorized_keys; fi",
    ]
  }
}
