#!/bin/bash
###############################################################################
# ovbuilder Installer
#
# Installs the ovbuilder CLI for building VMware VMs that register with OpenVox.
#
# Purpose of this script:
#   Many OpenVox operators do not use Python daily. Typing "pip" often fails
#   because it is not in PATH or points to the wrong Python.
#
#   This installer:
#     • Finds a real Python 3.9+ interpreter on your system
#     • Creates its own virtual environment
#     • Uses ONLY the venv's pip (never a bare "pip" command)
#     • Installs ovbuilder and wires up the `ovbuilder` command
#
# Usage:
#   ./install.sh
#   ./install.sh -y
#   ./install.sh --user
#   ./install.sh --uninstall
###############################################################################

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${CYAN}→${NC} $*"; }
log_ok()    { echo -e "${GREEN}✔${NC} $*"; }
log_warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
log_err()   { echo -e "${RED}✖${NC} $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo '0.1.0')"

# Defaults
INSTALL_DIR="/opt/ovbuilder"
USER_MODE=false
UNINSTALL=false
YES=false

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --user)
            USER_MODE=true
            shift
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        -y|--yes)
            YES=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [--user] [-y] [--uninstall]"
            exit 0
            ;;
        *)
            log_err "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [[ "$UNINSTALL" == true ]]; then
    log_info "Uninstalling ovbuilder"

    # Always clean system locations (may require sudo)
    if [ -L /usr/local/bin/ovbuilder ]; then
        sudo rm -f /usr/local/bin/ovbuilder 2>/dev/null || rm -f /usr/local/bin/ovbuilder
        log_ok "Removed system symlink /usr/local/bin/ovbuilder"
    fi
    if [ -d /opt/ovbuilder ]; then
        sudo rm -rf /opt/ovbuilder 2>/dev/null || rm -rf /opt/ovbuilder
        log_ok "Removed /opt/ovbuilder"
    fi

    # User locations
    if [ -L "$HOME/.local/bin/ovbuilder" ]; then
        rm -f "$HOME/.local/bin/ovbuilder"
        log_ok "Removed user symlink ~/.local/bin/ovbuilder"
    fi
    if [ -d "$HOME/.local/share/ovbuilder" ]; then
        rm -rf "$HOME/.local/share/ovbuilder"
        log_ok "Removed ~/.local/share/ovbuilder"
    fi

    log_ok "ovbuilder has been uninstalled."
    exit 0
fi

# Determine install location
if [[ "$USER_MODE" == true ]]; then
    INSTALL_DIR="$HOME/.local/share/ovbuilder"
    BIN_DIR="$HOME/.local/bin"
    USE_SUDO=false
else
    INSTALL_DIR="/opt/ovbuilder"
    BIN_DIR="/usr/local/bin"
    USE_SUDO=true
fi

VENV_DIR="${INSTALL_DIR}/venv"
OVBUILDER_BIN="${VENV_DIR}/bin/ovbuilder"
TARGET_LINK="${BIN_DIR}/ovbuilder"

log_info "Installing ovbuilder ${VERSION}"

# --- Find a good Python 3 ---
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -m venv --help >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    log_err "Could not find a suitable Python 3 interpreter with venv support."
    log_err "Please install Python 3.9 or newer (python3.11+ recommended)."
    exit 1
fi

log_ok "Using Python: $PYTHON ($($PYTHON --version 2>&1))"

# --- Decide on system vs user install automatically if needed ---
if [[ "$USER_MODE" == false && "$USE_SUDO" == true ]]; then
    if ! mkdir -p "$INSTALL_DIR" 2>/dev/null && [[ $EUID -ne 0 ]]; then
        log_warn "Cannot write to $INSTALL_DIR. Switching to user-mode install."
        USER_MODE=true
        INSTALL_DIR="$HOME/.local/share/ovbuilder"
        BIN_DIR="$HOME/.local/bin"
        USE_SUDO=false
    fi
fi

# --- Create directories ---
if [[ "$USE_SUDO" == true ]]; then
    sudo mkdir -p "$INSTALL_DIR" "$BIN_DIR"
else
    mkdir -p "$INSTALL_DIR" "$BIN_DIR"
fi

# --- Create virtual environment using the found Python ---
if [[ ! -d "$VENV_DIR" ]]; then
    log_info "Creating virtual environment at $VENV_DIR"
    if [[ "$USE_SUDO" == true ]]; then
        sudo "$PYTHON" -m venv "$VENV_DIR"
    else
        "$PYTHON" -m venv "$VENV_DIR"
    fi
    log_ok "Virtual environment created"
else
    log_info "Virtual environment already exists"
fi

VENV_PIP="${VENV_DIR}/bin/pip"
VENV_PYTHON="${VENV_DIR}/bin/python"

# Determine the correct way to run pip to avoid macOS (and other) cache/permission warnings.
# Never rely on a bare 'pip' command. Always go through the venv's pip.
# Use sudo -H only when the *script* needs to elevate (not already root).
# --no-cache-dir prevents cache permission warnings entirely when running under sudo.
if [[ "$USE_SUDO" == true && $EUID -ne 0 ]]; then
    PIP_CMD="sudo -H $VENV_PIP"
else
    PIP_CMD="$VENV_PIP"
fi

# pip preserves source file modes. A restrictive umask (or 640 sources) leaves
# site-packages as root:wheel mode 750/640 → non-root gets:
#   ModuleNotFoundError: No module named 'ovbuilder.main'
# Force a permissive umask for the install tree, then re-chmod after pip.
if [[ "$USE_SUDO" == true || $EUID -eq 0 ]]; then
    # Apply to this shell; also wrap pip with umask for sudo -H child shells.
    umask 022
fi

# --- Upgrade pip inside the virtual environment... ---
log_info "Upgrading pip inside the virtual environment..."
if [[ "$USE_SUDO" == true && $EUID -ne 0 ]]; then
    sudo -H bash -c "umask 022; '$VENV_PIP' install --quiet --no-cache-dir --upgrade pip"
else
    (umask 022; $PIP_CMD install --quiet --no-cache-dir --upgrade pip)
fi

# --- Install ovbuilder ---
log_info "Installing ovbuilder into the virtual environment..."

# For system installs (sudo), we do a regular (non-editable) install to avoid
# creating root-owned files like .egg-info in your source tree (which can cause
# "permission denied" or traversal issues for the normal user).
# Editable (-e) is only for --user or non-sudo dev installs.
if [[ "$USE_SUDO" == true ]]; then
    INSTALL_SPEC="."
else
    INSTALL_SPEC="-e ."
fi

if [[ "$USE_SUDO" == true && $EUID -ne 0 ]]; then
    sudo -H bash -c "umask 022; cd '$SCRIPT_DIR' && '$VENV_PIP' install --quiet --no-cache-dir --force-reinstall $INSTALL_SPEC"
else
    (umask 022; $PIP_CMD install --quiet --no-cache-dir --force-reinstall $INSTALL_SPEC)
fi

# Immediately fix site-packages perms (do not wait until end of script).
if [[ "$USE_SUDO" == true || $EUID -eq 0 ]]; then
    sudo chmod -R a+rX "$VENV_DIR/lib" 2>/dev/null || true
    sudo find "$VENV_DIR/bin" -type f -perm -u+x -exec chmod a+x {} + 2>/dev/null || true
fi

log_ok "ovbuilder package installed"

# --- Stage Terraform modules and ensure init ---
log_info "Staging Terraform provisioning modules..."
TF_SRC="$SCRIPT_DIR/terraform"
TF_DEST="$INSTALL_DIR/terraform"

# Preserve local Terraform state + provider cache across reinstalls.
# Wiping these caused "rebuild renames previous VM" confusion when a single
# shared state was recreated empty, and also loses tracking of existing VMs.
TF_PRESERVE_TMP=""
if [ -d "$TF_DEST" ]; then
    TF_PRESERVE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ovbuilder-tf-preserve.XXXXXX")"
    # State files (legacy shared state in module dir)
    for f in "$TF_DEST"/terraform.tfstate "$TF_DEST"/terraform.tfstate.backup \
             "$TF_DEST"/terraform.tfstate.* ; do
        if [ -e "$f" ]; then
            if [[ "$USE_SUDO" == true ]]; then
                sudo cp -a "$f" "$TF_PRESERVE_TMP/" 2>/dev/null || true
            else
                cp -a "$f" "$TF_PRESERVE_TMP/" 2>/dev/null || true
            fi
        fi
    done
    # Provider plugins / modules cache
    if [ -d "$TF_DEST/.terraform" ]; then
        if [[ "$USE_SUDO" == true ]]; then
            sudo cp -a "$TF_DEST/.terraform" "$TF_PRESERVE_TMP/" 2>/dev/null || true
        else
            cp -a "$TF_DEST/.terraform" "$TF_PRESERVE_TMP/" 2>/dev/null || true
        fi
    fi
    if [ -f "$TF_DEST/.terraform.lock.hcl" ]; then
        if [[ "$USE_SUDO" == true ]]; then
            sudo cp -a "$TF_DEST/.terraform.lock.hcl" "$TF_PRESERVE_TMP/" 2>/dev/null || true
        else
            cp -a "$TF_DEST/.terraform.lock.hcl" "$TF_PRESERVE_TMP/" 2>/dev/null || true
        fi
    fi
    log_info "Preserved existing Terraform state/cache (if any) before restage"
fi

if [[ "$USE_SUDO" == true ]]; then
    sudo rm -rf "$TF_DEST"
    sudo mkdir -p "$TF_DEST"
    sudo cp -a "$TF_SRC/." "$TF_DEST/"
else
    rm -rf "$TF_DEST"
    mkdir -p "$TF_DEST"
    cp -a "$TF_SRC/." "$TF_DEST/"
fi

# Restore preserved state/cache on top of freshly staged modules
if [ -n "$TF_PRESERVE_TMP" ] && [ -d "$TF_PRESERVE_TMP" ]; then
    if [[ "$USE_SUDO" == true ]]; then
        sudo cp -a "$TF_PRESERVE_TMP"/. "$TF_DEST/" 2>/dev/null || true
        sudo rm -rf "$TF_PRESERVE_TMP"
    else
        cp -a "$TF_PRESERVE_TMP"/. "$TF_DEST/" 2>/dev/null || true
        rm -rf "$TF_PRESERVE_TMP"
    fi
    log_ok "Restored preserved Terraform state/cache into $TF_DEST"
fi

if command -v terraform >/dev/null 2>&1; then
    if [ ! -d "$TF_DEST/.terraform" ] || [ ! -f "$TF_DEST/.terraform.lock.hcl" ]; then
        log_info "Running terraform init in $TF_DEST ..."
        if [[ "$USE_SUDO" == true ]]; then
            sudo -H terraform -chdir="$TF_DEST" init -upgrade -input=false
        else
            terraform -chdir="$TF_DEST" init -upgrade -input=false
        fi
        log_ok "terraform init complete."
    else
        log_info "Terraform already initialized (skipping init)."
    fi
else
    log_warn "terraform not found in PATH. You may need to install Terraform and run 'terraform -chdir=$TF_DEST init' manually."
fi

# Fix ownership so normal user can run terraform later (writes to .terraform/)
if [[ "$USE_SUDO" == true && -n "${SUDO_USER:-}" ]]; then
    REAL_USER="$SUDO_USER"
    REAL_GROUP=$(id -gn "$REAL_USER" 2>/dev/null || echo "$REAL_USER")
    sudo chown -R "$REAL_USER:$REAL_GROUP" "$TF_DEST"
    log_info "Terraform directory ownership set for $REAL_USER"
fi

log_ok "Terraform modules staged to $TF_DEST"

# Force correct permissions on the entry point and interpreter.
# This prevents "permission denied" when the venv was created under sudo
# and umask was restrictive (common on macOS).
if [[ "$USE_SUDO" == true ]]; then
    sudo chmod 755 "$OVBUILDER_BIN" 2>/dev/null || true
    sudo chmod 755 "$VENV_DIR/bin" 2>/dev/null || true
    for pybin in "$VENV_DIR/bin/python" "$VENV_DIR/bin/python3" "$VENV_DIR/bin/python"*; do
        [ -f "$pybin" ] && sudo chmod 755 "$pybin" 2>/dev/null || true
    done
else
    chmod 755 "$OVBUILDER_BIN" 2>/dev/null || true
    chmod 755 "$VENV_DIR/bin" 2>/dev/null || true
fi

# --- Create or update the symlink ---
if [[ "$USE_SUDO" == true ]]; then
    sudo mkdir -p "$BIN_DIR"
    sudo ln -sf "$OVBUILDER_BIN" "$TARGET_LINK"
else
    mkdir -p "$BIN_DIR"
    ln -sf "$OVBUILDER_BIN" "$TARGET_LINK"
fi

log_ok "Symlink created: $TARGET_LINK → $OVBUILDER_BIN"

echo "Installed command location: $TARGET_LINK"
if [ -L "$TARGET_LINK" ]; then
  echo "  Points to: $(readlink -f "$TARGET_LINK" 2>/dev/null || readlink "$TARGET_LINK")"
fi

echo ""
echo "To use 'ovbuilder' in this shell right now:"
echo "  export PATH=\"$BIN_DIR:\$PATH\""
echo "  hash -r"
echo "  ovbuilder build"
echo ""

# --- Proactive permission fix (the script knows when it used sudo) ---
# When sudo was used for install, the files are root-owned.
# We must guarantee the thing the user types ("ovbuilder") is executable
# by the non-root user right now, before the script exits.
chmod +x "$TARGET_LINK" 2>/dev/null || sudo chmod +x "$TARGET_LINK" 2>/dev/null || true

if [ -L "$TARGET_LINK" ]; then
    real_target=$(readlink -f "$TARGET_LINK" 2>/dev/null || readlink "$TARGET_LINK")
    if [ -n "$real_target" ] && [ -f "$real_target" ]; then
        chmod +x "$real_target" 2>/dev/null || sudo chmod +x "$real_target" 2>/dev/null || true
    fi
fi

# Also ensure the python the wrapper calls is executable
for py in "$VENV_DIR/bin/python" "$VENV_DIR/bin/python3"*; do
    if [ -f "$py" ]; then
        chmod +x "$py" 2>/dev/null || sudo chmod +x "$py" 2>/dev/null || true
    fi
done

if [[ "$USE_SUDO" == true || $EUID -eq 0 ]]; then
    # Make the entire venv readable and directories traversable by others.
    # pyvenv.cfg and other config files need to be readable by the user
    # who will run the python from the venv.
    sudo chmod -R a+rX "$VENV_DIR" 2>/dev/null || true
    # Explicitly ensure pyvenv.cfg is readable (site module reads it)
    sudo chmod 644 "$VENV_DIR/pyvenv.cfg" 2>/dev/null || true
    # Force execute on all binaries in bin/
    sudo find "$VENV_DIR/bin" -type f -perm -u+x -exec chmod a+x {} + 2>/dev/null || true
    log_ok "Sudo install detected — full venv permissions normalized for normal user."
fi

# --- Verify import as the *invoking* user (not root) ---
# Catches root-only site-packages (mode 750/640) that root can import fine.
VERIFY_USER="${SUDO_USER:-$USER}"
VERIFY_CMD="'$VENV_PYTHON' -c 'from ovbuilder.main import cli'"
if [[ "$USE_SUDO" == true && -n "${SUDO_USER:-}" && $EUID -eq 0 ]]; then
    if ! sudo -u "$SUDO_USER" -H bash -c "cd /tmp && $VERIFY_CMD" 2>/dev/null; then
        log_warn "Import check as $SUDO_USER failed — re-applying a+rX on venv"
        sudo chmod -R a+rX "$VENV_DIR" 2>/dev/null || true
    fi
elif [[ "$USE_SUDO" == true && $EUID -ne 0 ]]; then
    if ! (cd /tmp && eval "$VERIFY_CMD") 2>/dev/null; then
        log_warn "Import check failed — re-applying a+rX on venv"
        sudo chmod -R a+rX "$VENV_DIR" 2>/dev/null || true
    fi
fi

if ! (cd /tmp && eval "$VERIFY_CMD") 2>/dev/null; then
    log_err "ovbuilder installed but cannot import ovbuilder.main as $VERIFY_USER."
    log_err "Likely site-packages permissions (need world-readable under $VENV_DIR)."
    log_err "Try: sudo chmod -R a+rX $VENV_DIR"
    exit 1
fi
log_ok "Import check passed (from ovbuilder.main import cli)"

# --- Verify and give PATH help ---
if command -v ovbuilder >/dev/null 2>&1; then
    INSTALLED_VERSION=$(ovbuilder --version 2>/dev/null | head -1 || echo "ovbuilder")
    log_ok "Installation successful. Command available: $INSTALLED_VERSION"
    echo
    echo "Try it now:"
    echo "    ovbuilder build"
else
    log_warn "Symlink created at $TARGET_LINK"
    echo
    echo "The 'ovbuilder' command may not be in your PATH yet (very common on macOS)."
    echo
    echo "Run these commands to use it immediately:"
    echo "    export PATH=\"${BIN_DIR}:\$PATH\""
    echo "    hash -r"
    echo "    ovbuilder build"
    echo
    echo "For it to work in new terminals, add this line to your ~/.zshrc (zsh) or ~/.bash_profile:"
    echo "    export PATH=\"${BIN_DIR}:\$PATH\""
    echo "Then restart your terminal or run 'source ~/.zshrc'."
fi

echo
echo "To uninstall later:"
echo "    $0 --uninstall"
echo

# Final hard guarantee: make the delivered command executable for the invoking user.
# This runs no matter what, and uses sudo if the files are root-owned.
if [ -L /usr/local/bin/ovbuilder ]; then
    sudo chmod +x /usr/local/bin/ovbuilder 2>/dev/null || chmod +x /usr/local/bin/ovbuilder 2>/dev/null || true
    real=$(readlink -f /usr/local/bin/ovbuilder 2>/dev/null || true)
    if [ -n "$real" ] && [ -f "$real" ]; then
        sudo chmod +x "$real" 2>/dev/null || chmod +x "$real" 2>/dev/null || true
    fi
fi

if [ -L "$HOME/.local/bin/ovbuilder" ]; then
    chmod +x "$HOME/.local/bin/ovbuilder" 2>/dev/null || true
    real=$(readlink -f "$HOME/.local/bin/ovbuilder" 2>/dev/null || true)
    if [ -n "$real" ] && [ -f "$real" ]; then
        chmod +x "$real" 2>/dev/null || true
    fi
fi

# Make sure parent directories are searchable by others (critical for sudo installs on macOS)
if [[ "$USE_SUDO" == true || $EUID -eq 0 ]]; then
    sudo chmod +x /opt/ovbuilder 2>/dev/null || true
    sudo chmod +x /opt/ovbuilder/venv 2>/dev/null || true
    sudo chmod +x /opt/ovbuilder/venv/bin 2>/dev/null || true
fi

log_ok "Done."

# Note: sudo installs are non-editable to avoid root .egg-info in source (fixes permission denied on user files after sudo pip -e)
# pyvenv.cfg and venv files now get a+rX for sudo installs to fix PermissionError on site import

# === FINAL PERMISSION FIX (always run) ===
# This ensures the symlink and pyvenv.cfg are usable by the normal user.
# Use -h for the symlink so we chmod the link itself, not the target.
if [ -L /usr/local/bin/ovbuilder ]; then
    sudo chmod -h 755 /usr/local/bin/ovbuilder 2>/dev/null || chmod -h 755 /usr/local/bin/ovbuilder 2>/dev/null || true
fi
if [ -f /opt/ovbuilder/venv/pyvenv.cfg ]; then
    sudo chmod 644 /opt/ovbuilder/venv/pyvenv.cfg 2>/dev/null || true
fi
if [ -d /opt/ovbuilder ]; then
    sudo chmod 755 /opt/ovbuilder 2>/dev/null || true
    sudo chmod 755 /opt/ovbuilder/venv 2>/dev/null || true
    sudo chmod 755 /opt/ovbuilder/venv/bin 2>/dev/null || true
fi
# For user mode too
if [ -L "$HOME/.local/bin/ovbuilder" ]; then
    chmod -h 755 "$HOME/.local/bin/ovbuilder" 2>/dev/null || true
fi
if [ -f "$HOME/.local/share/ovbuilder/venv/pyvenv.cfg" ]; then
    chmod 644 "$HOME/.local/share/ovbuilder/venv/pyvenv.cfg" 2>/dev/null || true
fi
