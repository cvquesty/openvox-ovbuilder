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

# Inventory lookups live in modules/vm so standalone ESXi hosts (SEA3) are
# not forced through data.vsphere_compute_cluster at the root module.
