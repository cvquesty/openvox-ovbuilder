# =============================================================================
# ITSYS VMware VM Provisioner (ISO-based)
# Variables for the reusable VM module
# =============================================================================

variable "vm_name" {
  description = "Name of the virtual machine to create"
  type        = string
}

variable "datacenter" {
  description = "vSphere datacenter name"
  type        = string
}

variable "cluster" {
  description = "vSphere compute cluster name"
  type        = string
}

variable "vm_datastore" {
  description = "Datastore where the VM files (config + disks) will be created"
  type        = string
}

variable "iso_datastore" {
  description = "Datastore containing the OS ISO images. Defaults to vm_datastore if not set."
  type        = string
  default     = null
}

variable "iso_path" {
  description = "Path to the ISO file on the iso_datastore (e.g. isos/almalinux-9.4-x86_64-dvd.iso). Must start with / if full path from datastore root."
  type        = string
}

variable "networks" {
  description = "List of portgroup/network names to attach. First entry is primary NIC."
  type        = list(string)
  default     = ["VM Network"]
}

variable "num_cpus" {
  description = "Number of virtual CPUs"
  type        = number
  default     = 2
}

variable "memory_mb" {
  description = "Memory in MiB"
  type        = number
  default     = 4096
}

variable "disk_size_gb" {
  description = "Size of the primary OS disk in GB"
  type        = number
  default     = 80
}

variable "guest_id" {
  description = "vSphere guest ID (e.g. rhel9_64Guest, centos9_64Guest, ubuntu64Guest, otherLinux64Guest). Use PowerCLI or docs to find valid values for your hosts."
  type        = string
  default     = "rhel9_64Guest"
}

variable "firmware" {
  description = "Firmware type. 'efi' (recommended for modern OS) or 'bios'."
  type        = string
  default     = "efi"
  validation {
    condition     = contains(["efi", "bios"], var.firmware)
    error_message = "firmware must be 'efi' or 'bios'."
  }
}

variable "efi_secure_boot_enabled" {
  description = "Enable EFI Secure Boot (only valid with firmware = 'efi')"
  type        = bool
  default     = false
}

variable "folder" {
  description = "Optional VM folder path relative to datacenter (e.g. ITSYS/Dev). Leave empty for root."
  type        = string
  default     = ""
}

variable "domain" {
  description = "DNS domain suffix (used for linux_options if customization is added later)"
  type        = string
  default     = "example.com"
}

variable "boot_delay_ms" {
  description = "Milliseconds to wait before starting boot sequence. Useful to ensure ISO is attached and detected."
  type        = number
  default     = 10000
}

variable "tags" {
  description = "Set of tag IDs (not names) to apply to the VM. Leave empty unless you manage tags via Terraform or know the IDs."
  type        = set(string)
  default     = []
}

variable "enable_disk_uuid" {
  description = "Expose disk UUIDs to the guest (disk.EnableUUID). Useful for some storage scenarios."
  type        = bool
  default     = false
}
