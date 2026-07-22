# Root Terraform version + local backend (path set per-VM via -backend-config).
# Replaces deprecated CLI flags: -state / -state-out / -backup.

terraform {
  required_version = ">= 1.3.0"

  # Path is injected by ovbuilder at init time:
  #   terraform init -reconfigure -backend-config="path=.../terraform.tfstate"
  backend "local" {}

  required_providers {
    vsphere = {
      source  = "vmware/vsphere"
      version = ">= 2.0.0"
    }
  }
}
