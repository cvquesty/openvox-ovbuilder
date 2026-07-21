#!/bin/bash
# AlmaLinux / RHEL-family packages for ovbuilder goldens.
set -euo pipefail

dnf -y update || true
dnf -y install \
  open-vm-tools \
  cloud-init \
  cloud-utils-growpart \
  gdisk \
  qemu-guest-agent \
  sudo \
  curl \
  wget \
  vim-minimal \
  tar \
  || true

systemctl enable vmtoolsd.service 2>/dev/null || systemctl enable open-vm-tools.service 2>/dev/null || true
systemctl enable cloud-init.service cloud-init-local.service cloud-config.service cloud-final.service 2>/dev/null || true

# Passwordless sudo for the admin user (created by kickstart)
if id almalinux &>/dev/null; then
  echo 'almalinux ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/almalinux
  chmod 440 /etc/sudoers.d/almalinux
fi
if id cloud-user &>/dev/null; then
  echo 'cloud-user ALL=(ALL) NOPASSWD:ALL' >/etc/sudoers.d/cloud-user
  chmod 440 /etc/sudoers.d/cloud-user
fi

dnf clean all || true
