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

# Password-required sudo via wheel. Do not grant NOPASSWD to build users.
if getent group wheel >/dev/null; then
  echo '%wheel ALL=(ALL) ALL' >/etc/sudoers.d/wheel
  chmod 440 /etc/sudoers.d/wheel
fi
rm -f /etc/sudoers.d/almalinux /etc/sudoers.d/cloud-user

dnf clean all || true
