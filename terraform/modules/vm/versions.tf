terraform {
  required_version = ">= 1.5"

  required_providers {
    vsphere = {
      source  = "vmware/vsphere"
      version = "~> 2.7"
    }
  }
}
