locals {
  vm = try(vsphere_virtual_machine.from_template[0], vsphere_virtual_machine.from_iso[0])
}

output "vm_id" {
  description = "Managed Object ID (MOID) of the created virtual machine"
  value       = local.vm.id
}

output "vm_name" {
  description = "Name of the created virtual machine"
  value       = local.vm.name
}

output "default_ip_address" {
  description = "Default IP reported by Tools (may be empty until guestinfo/cloud-init finishes)"
  value       = local.vm.default_ip_address
}

output "network_interface_ids" {
  description = "Map of network name to the Terraform device key for each NIC"
  value       = local.vm.network_interface[*].device_address
}

output "iso_attached" {
  description = "ISO path attached (iso mode only; empty for clone)"
  value       = var.provision_mode == "iso" ? var.iso_path : ""
}

output "provision_mode" {
  value = var.provision_mode
}

output "template_name" {
  value = var.template_name
}
