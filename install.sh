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

# --- Upgrade pip inside the venv (using venv python, never bare 'pip') ---
log_info "Upgrading pip inside the virtual environment..."
$PIP_CMD install --quiet --no-cache-dir --upgrade pip

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

$PIP_CMD install --quiet --no-cache-dir --force-reinstall $INSTALL_SPEC

log_ok "ovbuilder package installed"

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
