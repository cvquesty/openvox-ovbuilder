# =============================================================================
# ovbuilder VM module variables
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

variable "provision_mode" {
  description = "clone = Packer golden template; iso = legacy empty disk + ISO"
  type        = string
  default     = "clone"
  validation {
    condition     = contains(["clone", "iso"], var.provision_mode)
    error_message = "provision_mode must be \"clone\" or \"iso\"."
  }
}

variable "template_name" {
  description = "vSphere template name (required when provision_mode = clone)"
  type        = string
  default     = ""
}

variable "iso_datastore" {
  description = "Datastore containing OS ISOs (iso mode). Defaults to vm_datastore."
  type        = string
  default     = null
}

variable "iso_path" {
  description = "Path to ISO on iso_datastore (iso mode only)"
  type        = string
  default     = ""
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
  description = "Size of the primary OS disk in GB (must be >= template disk in clone mode)"
  type        = number
  default     = 80
}

variable "guest_id" {
  description = "vSphere guest ID (empty = inherit from template in clone mode)"
  type        = string
  default     = ""
}

variable "firmware" {
  description = "Firmware type: efi or bios"
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
  description = "Optional VM folder path relative to datacenter"
  type        = string
  default     = ""
}

variable "domain" {
  description = "DNS domain suffix"
  type        = string
  default     = "example.com"
}

variable "boot_delay_ms" {
  description = "ISO mode: ms to wait before boot so CD-ROM is detected"
  type        = number
  default     = 10000
}

variable "tags" {
  description = "Set of tag IDs to apply to the VM"
  type        = set(string)
  default     = []
}

variable "enable_disk_uuid" {
  description = "Expose disk UUIDs to the guest (disk.EnableUUID)"
  type        = bool
  default     = false
}

variable "guestinfo_extra_config" {
  description = "Map of extra_config keys for cloud-init guestinfo (clone mode)"
  type        = map(string)
  default     = {}
}

variable "wait_for_guest_net_timeout" {
  description = "Clone mode: minutes to wait for guest network (0 = do not wait/fail)"
  type        = number
  default     = 0
}

variable "wait_for_guest_ip_timeout" {
  description = "Clone mode: minutes to wait for guest IP (0 = do not wait/fail)"
  type        = number
  default     = 0
}
