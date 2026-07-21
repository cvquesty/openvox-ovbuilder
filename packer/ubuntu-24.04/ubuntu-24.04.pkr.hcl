# Packer: Ubuntu 24.04 → vSphere template (NO DHCP / NO SSH to guest)
#
# Autoinstall + late-commands bake tools/cloud-init; shutdown: poweroff.
# Packer communicator=none waits for poweroff, then convert_to_template.

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

  cd_content = {
    "user-data" = file("${path.cwd}/ubuntu-24.04/http/user-data")
    "meta-data" = file("${path.cwd}/ubuntu-24.04/http/meta-data")
  }
  cd_label = "cidata"

  boot_wait = "5s"
  boot_command = [
    "e<wait>",
    "<down><down><down><end>",
    " autoinstall ds=nocloud\\;s=/cdrom/",
    "<f10>",
  ]

  communicator     = "none"
  # Autoinstall + late-commands need far more than the 5m default.
  shutdown_timeout = "120m"

  convert_to_template = true
  remove_cdrom        = true
}

build {
  sources = ["source.vsphere-iso.ubuntu2404"]
}
