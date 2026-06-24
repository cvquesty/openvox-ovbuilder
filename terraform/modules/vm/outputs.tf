output "vm_id" {
  description = "Managed Object ID (MOID) of the created virtual machine"
  value       = vsphere_virtual_machine.vm.id
}

output "vm_name" {
  description = "Name of the created virtual machine"
  value       = vsphere_virtual_machine.vm.name
}

output "default_ip_address" {
  description = "Default IP address reported by the VM (empty until VMware Tools + guest reports after OS install)"
  value       = vsphere_virtual_machine.vm.default_ip_address
}

output "network_interface_ids" {
  description = "Map of network name to the Terraform device key for each NIC"
  value       = vsphere_virtual_machine.vm.network_interface[*].device_address
}

output "iso_attached" {
  description = "Confirmation of the ISO path that was attached"
  value       = var.iso_path
}
