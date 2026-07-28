"""
ovbuilder — OpenVox VM Builder

Rapid provisioning of VMware virtual machines that join an OpenVox/Puppet
fleet: Packer golden clone (default) or legacy ISO attach, with per-VM
Terraform state and optional agent bootstrap.
"""

# Single source for setuptools / importlib when VERSION file is absent.
# Keep in sync with root VERSION via release process (install / bump).
__version__ = "0.97-beta15"
