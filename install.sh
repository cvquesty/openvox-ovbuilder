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
    if [[ "$USER_MODE" == true || -d "$HOME/.local/share/ovbuilder" ]]; then
        INSTALL_DIR="$HOME/.local/share/ovbuilder"
        BIN_LINK="$HOME/.local/bin/ovbuilder"
    else
        INSTALL_DIR="/opt/ovbuilder"
        BIN_LINK="/usr/local/bin/ovbuilder"
    fi

    log_info "Uninstalling ovbuilder from $INSTALL_DIR"
    if [[ -L "$BIN_LINK" ]]; then
        sudo rm -f "$BIN_LINK" 2>/dev/null || rm -f "$BIN_LINK"
        log_ok "Removed symlink $BIN_LINK"
    fi
    if [[ -d "$INSTALL_DIR" ]]; then
        sudo rm -rf "$INSTALL_DIR" 2>/dev/null || rm -rf "$INSTALL_DIR"
        log_ok "Removed $INSTALL_DIR"
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

# --- Upgrade pip inside the venv (using venv python, never bare 'pip') ---
log_info "Upgrading pip inside the virtual environment..."
"$VENV_PIP" install --quiet --upgrade pip

# --- Install ovbuilder ---
log_info "Installing ovbuilder into the virtual environment..."

# We use -e (editable) by default so that changes in the source tree are picked up.
# This is convenient for both development and when running from a git checkout.
if [[ "$USE_SUDO" == true ]]; then
    sudo "$VENV_PIP" install --quiet -e "$SCRIPT_DIR"
else
    "$VENV_PIP" install --quiet -e "$SCRIPT_DIR"
fi

log_ok "ovbuilder package installed"

# --- Create or update the symlink ---
if [[ "$USE_SUDO" == true ]]; then
    sudo mkdir -p "$BIN_DIR"
    sudo ln -sf "$OVBUILDER_BIN" "$TARGET_LINK"
    sudo chmod +x "$TARGET_LINK" 2>/dev/null || true
else
    mkdir -p "$BIN_DIR"
    ln -sf "$OVBUILDER_BIN" "$TARGET_LINK"
    chmod +x "$TARGET_LINK" 2>/dev/null || true
fi

log_ok "Symlink created: $TARGET_LINK → $OVBUILDER_BIN"

# --- Verify ---
if command -v ovbuilder >/dev/null 2>&1; then
    INSTALLED_VERSION=$(ovbuilder --version 2>/dev/null | head -1 || echo "ovbuilder")
    log_ok "Installation successful. Command available: $INSTALLED_VERSION"
else
    log_warn "Symlink created at $TARGET_LINK"
    log_warn "If 'ovbuilder' is not found, add this to your shell profile:"
    log_warn "    export PATH=\"${BIN_DIR}:\$PATH\""
fi

echo
echo -e "${BOLD}Next step:${NC}"
echo "    ovbuilder build"
echo
echo "To uninstall:"
echo "    $0 --uninstall"
echo

log_ok "Done."