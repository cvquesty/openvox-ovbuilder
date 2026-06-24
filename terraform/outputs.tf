# =============================================================================
# Outputs (proxied from the VM module)
# =============================================================================

output "vm_id" {
  description = "MOID of the provisioned VM"
  value       = module.vm.vm_id
}

output "vm_name" {
  description = "Name of the provisioned VM"
  value       = module.vm.vm_name
}

output "vm_default_ip_address" {
  description = "Reported IP (empty until after OS install + VMware Tools)"
  value       = module.vm.default_ip_address
}

output "iso_attached" {
  description = "ISO that was attached for installation"
  value       = module.vm.iso_attached
}
