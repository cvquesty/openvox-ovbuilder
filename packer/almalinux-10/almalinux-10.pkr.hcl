# Packer: AlmaLinux 10 → vSphere template (NO DHCP / NO SSH to guest)
#
# Flow:
#   1. Attach install ISO + OEMDRV kickstart CD
#   2. Boot, unattended install, %post bakes tools/cloud-init/cleanup
#   3. Kickstart `poweroff` — Packer sees powered-off VM
#   4. remove_cdrom + convert_to_template
#
# Clone identity (hostname, static IP, …) is applied later by ovbuilder
# via guestinfo / cloud-init — never during this golden build.

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

  # NIC still attached so clones inherit a vNIC; no DHCP used during build.
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

  # Kickstart on second CD (no HTTP to the laptop)
  cd_content = {
    "ks.cfg" = file("${path.cwd}/almalinux-10/http/ks.cfg")
  }
  cd_label = "OEMDRV"

  boot_wait = "8s"
  boot_command = [
    "e<down><down><end> inst.ks=cdrom:/dev/sr1:/ks.cfg<leftCtrlOn>x<leftCtrlOff>"
  ]

  # No guest IP, no SSH — wait for kickstart `poweroff`.
  communicator = "none"
  # Full DVD install + %post can take well over 5m on shared storage.
  shutdown_timeout = "120m"

  convert_to_template = true
  remove_cdrom        = true
}

build {
  sources = ["source.vsphere-iso.almalinux10"]
  # No shell provisioners — everything is in kickstart %post.
}
