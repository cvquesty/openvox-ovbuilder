# =============================================================================
# ovbuilder root module
#
# Thin wrapper: all VM logic lives in modules/vm. Root only wires variables
# from the CLI-generated var-file (or terraform.tfvars) into the module.
# Provider configuration is in providers.tf.
# =============================================================================

module "vm" {
  source = "./modules/vm"

  vm_name       = var.vm_name
  datacenter    = var.vsphere_datacenter
  cluster       = var.vsphere_cluster
  vm_datastore  = var.vm_datastore
  iso_datastore = var.iso_datastore
  iso_path      = var.iso_path

  provision_mode         = var.provision_mode
  template_name          = var.template_name
  guestinfo_extra_config = var.guestinfo_extra_config
  guest_id               = var.guest_id

  networks     = var.networks
  num_cpus     = var.num_cpus
  memory_mb    = var.memory_mb
  disk_size_gb = var.disk_size_gb
  firmware     = var.firmware
  domain       = var.domain
  folder       = var.folder
}
