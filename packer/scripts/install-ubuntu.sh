#!/bin/bash
# Ubuntu packages for ovbuilder goldens.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get -y upgrade || true
apt-get -y install \
  open-vm-tools \
  cloud-init \
  cloud-guest-utils \
  gdisk \
  qemu-guest-agent \
  sudo \
  curl \
  wget \
  vim-tiny \
  tar \
  || true

systemctl enable open-vm-tools.service 2>/dev/null || true
systemctl enable cloud-init.service cloud-init-local.service cloud-config.service cloud-final.service 2>/dev/null || true

# Password-required sudo comes from the sudo group. Do not grant NOPASSWD.
rm -f /etc/sudoers.d/ubuntu

apt-get clean
rm -rf /var/lib/apt/lists/*
