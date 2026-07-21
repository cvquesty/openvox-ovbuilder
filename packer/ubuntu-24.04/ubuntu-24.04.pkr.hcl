# Packer: Ubuntu 24.04 → vSphere template for ovbuilder
# Usage: packer init . && packer build -var-file=../variables.auto.pkrvars.hcl .

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
variable "vcenter_password" { type = string, sensitive = true }
variable "datacenter" { type = string }
variable "cluster" { type = string }
variable "datastore" { type = string }
variable "network" { type = string }
variable "folder" { type = string, default = "" }
variable "iso_datastore" { type = string, default = "" }
variable "insecure_connection" { type = bool, default = true }
variable "ssh_password" { type = string, default = "ChangeMe-BuildOnly!", sensitive = true }
variable "ssh_public_key" { type = string, default = "" }

variable "template_name" {
  type    = string
  default = "ovbuilder-ubuntu-24.04"
}

variable "iso_path" {
  type    = string
  default = "isos/ubuntu-24.04-live-server-amd64.iso"
}

variable "cpu" { type = number, default = 2 }
variable "memory_mb" { type = number, default = 4096 }
variable "disk_gb" { type = number, default = 40 }

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

  http_directory = "${path.root}/http"
  boot_wait      = "5s"
  # Ubuntu live server autoinstall (GRUB edit)
  boot_command = [
    "e<wait>",
    "<down><down><down><end>",
    " autoinstall ds=nocloud-net\\;s=http://{{ .HTTPIP }}:{{ .HTTPPort }}/",
    "<f10>",
  ]

  ssh_username           = "ubuntu"
  ssh_password           = var.ssh_password
  ssh_timeout            = "60m"
  ssh_handshake_attempts = 100

  shutdown_command    = "echo '${var.ssh_password}' | sudo -S /sbin/shutdown -h now"
  convert_to_template = true
  remove_cdrom        = true
}

build {
  sources = ["source.vsphere-iso.ubuntu2404"]

  provisioner "shell" {
    execute_command = "echo '${var.ssh_password}' | {{ .Vars }} sudo -S -E bash '{{ .Path }}'"
    scripts = [
      "${path.root}/../scripts/install-ubuntu.sh",
      "${path.root}/../scripts/cleanup-linux.sh",
    ]
  }

  provisioner "shell" {
    execute_command = "echo '${var.ssh_password}' | {{ .Vars }} sudo -S -E bash -c '{{ .Vars }} {{ .Path }}'"
    inline = [
      "if [ -n '${var.ssh_public_key}' ]; then mkdir -p /home/ubuntu/.ssh && echo '${var.ssh_public_key}' >> /home/ubuntu/.ssh/authorized_keys && chown -R ubuntu:ubuntu /home/ubuntu/.ssh && chmod 700 /home/ubuntu/.ssh && chmod 600 /home/ubuntu/.ssh/authorized_keys; fi",
    ]
  }
}
