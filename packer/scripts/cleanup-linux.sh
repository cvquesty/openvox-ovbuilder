#!/bin/bash
# Shared first-boot hygiene for ovbuilder golden images.
# Runs at the end of Packer provisioners (as root).
set -euo pipefail

echo "==> ovbuilder golden cleanup"

# cloud-init must re-run on first clone boot
if command -v cloud-init >/dev/null 2>&1; then
  cloud-init clean --logs --seed || cloud-init clean --logs || true
fi

# Unique machine identity per clone
truncate -s 0 /etc/machine-id 2>/dev/null || true
rm -f /var/lib/dbus/machine-id 2>/dev/null || true
ln -sf /etc/machine-id /var/lib/dbus/machine-id 2>/dev/null || true

# Host keys regenerate on first boot (ssh.service / cloud-init)
rm -f /etc/ssh/ssh_host_* 2>/dev/null || true

# No residual network static config from the build VM
rm -f /etc/netplan/50-cloud-init.yaml 2>/dev/null || true
rm -f /etc/sysconfig/network-scripts/ifcfg-eth0 2>/dev/null || true
rm -f /etc/NetworkManager/system-connections/* 2>/dev/null || true

# Logs / history
rm -rf /var/log/* 2>/dev/null || true
rm -f /root/.bash_history /home/*/.bash_history 2>/dev/null || true
history -c 2>/dev/null || true

# Temp
rm -rf /tmp/* /var/tmp/* 2>/dev/null || true

# Ensure cloud-init VMware guestinfo datasource is preferred
mkdir -p /etc/cloud/cloud.cfg.d
cat >/etc/cloud/cloud.cfg.d/99-ovbuilder-vmware.cfg <<'EOF'
datasource_list: [ VMware, OVF, None ]
datasource:
  VMware:
    allow_raw_data: true
EOF

# Disable cloud-init network wait that can hang offline labs (optional)
# Leave network config to guestinfo userdata from ovbuilder.

sync
echo "==> cleanup complete"
