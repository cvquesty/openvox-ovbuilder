# =============================================================================
# Provider Configuration
# =============================================================================

# vSphere Provider
# Docs: https://registry.terraform.io/providers/vmware/vsphere/latest/docs
provider "vsphere" {
  # Authentication can be provided via:
  # - Environment variables (VSPHERE_USER, VSPHERE_PASSWORD, VSPHERE_SERVER)
  # - terraform.tfvars (see terraform.tfvars.example)
  # - Or explicit values below (not recommended for source control)

  user           = var.vsphere_user
  password       = var.vsphere_password
  vsphere_server = var.vsphere_server

  # Set to true only in lab environments. Prefer proper certs in production.
  allow_unverified_ssl = var.vsphere_allow_unverified_ssl
}

# -----------------------------------------------------------------------------
# Optional root-level data sources
#
# The ISO VM provisioner module (modules/vm) performs its own lookups,
# so these are mainly useful if you add other resources at the root level.
# -----------------------------------------------------------------------------

data "vsphere_datacenter" "dc" {
  name = var.vsphere_datacenter
}

data "vsphere_compute_cluster" "cluster" {
  name          = var.vsphere_cluster
  datacenter_id = data.vsphere_datacenter.dc.id
}
