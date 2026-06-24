# =============================================================================
# ITSYS Root Module - ISO-based VM Provisioner Example
# =============================================================================

# This root shows the simplest way to use the reusable provisioner module.
# For day-to-day use, just edit terraform.tfvars and run:
#
#   terraform apply -var="vm_name=newsrv42"
#
# or pass variables on the command line.

module "vm" {
  source = "./modules/vm"

  # Required identification and location
  vm_name       = var.vm_name
  datacenter    = var.vsphere_datacenter
  cluster       = var.vsphere_cluster
  vm_datastore  = var.vm_datastore
  iso_datastore = var.iso_datastore
  iso_path      = var.iso_path

  # Networking (order is preserved)
  networks = var.networks

  # Hardware
  num_cpus     = var.num_cpus
  memory_mb    = var.memory_mb
  disk_size_gb = var.disk_size_gb

  # Guest OS
  guest_id = var.guest_id
  firmware = var.firmware
  domain   = var.domain
  folder   = var.folder
}

# You can add additional modules here for more VMs or other resources.
# Example of a second VM:
#
# module "vm2" {
#   source = "./modules/vm"
#   vm_name = "itsys-db01"
#   ...
# }
