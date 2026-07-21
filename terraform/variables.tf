# =============================================================================
# vSphere Connection (used by provider)
# =============================================================================

variable "vsphere_user" {
  description = "vSphere username"
  type        = string
  sensitive   = true
}

variable "vsphere_password" {
  description = "vSphere password"
  type        = string
  sensitive   = true
}

variable "vsphere_server" {
  description = "vSphere server FQDN (e.g. vcenter.example.com)"
  type        = string
}

variable "vsphere_allow_unverified_ssl" {
  description = "Allow unverified SSL certificates (set true only for labs)"
  type        = bool
  default     = false
}

# =============================================================================
# Common vSphere Location (passed down to modules)
# =============================================================================

variable "vsphere_datacenter" {
  description = "Name of the vSphere datacenter"
  type        = string
}

variable "vsphere_cluster" {
  description = "Name of the compute cluster"
  type        = string
}

# =============================================================================
# VM Provisioner Variables (ISO-based)
# =============================================================================

variable "vm_name" {
  description = "Hostname of the new VM (required)"
  type        = string
}

variable "vm_datastore" {
  description = "Datastore to place the VM files and disks"
  type        = string
}

variable "iso_datastore" {
  description = "Datastore that holds the OS ISO images (can be the same as vm_datastore)"
  type        = string
  default     = null
}

variable "iso_path" {
  description = "Path to the ISO inside iso_datastore (iso mode only)"
  type        = string
  default     = ""
}

variable "provision_mode" {
  description = "clone (Packer golden) or iso (legacy)"
  type        = string
  default     = "clone"
}

variable "template_name" {
  description = "vSphere template name when provision_mode = clone"
  type        = string
  default     = ""
}

variable "guestinfo_extra_config" {
  description = "cloud-init guestinfo map (base64 payloads) for clone mode"
  type        = map(string)
  default     = {}
}

variable "guest_id" {
  description = "Override guest_id (empty = template default in clone mode)"
  type        = string
  default     = ""
}

variable "networks" {
  description = "List of vSphere port groups / networks to attach (order matters)"
  type        = list(string)
  default     = ["VM Production"]
}

variable "num_cpus" {
  description = "Number of vCPUs"
  type        = number
  default     = 2
}

variable "memory_mb" {
  description = "Memory size in MiB"
  type        = number
  default     = 4096
}

variable "disk_size_gb" {
  description = "Size of the OS disk in GB"
  type        = number
  default     = 80
}

variable "guest_id" {
  description = "vSphere guest operating system identifier"
  type        = string
  default     = "rhel9_64Guest"
}

variable "firmware" {
  description = "Firmware type: efi or bios"
  type        = string
  default     = "efi"
}

variable "domain" {
  description = "DNS domain for documentation / future customization"
  type        = string
  default     = "example.com"
}

variable "folder" {
  description = "Optional folder path for the VM (relative to datacenter VM folder)"
  type        = string
  default     = ""
}

