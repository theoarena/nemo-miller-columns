#!/usr/bin/env bash
# Install the Miller Columns extension into the per-user nemo-python directory.
#
# Copies nemo_miller_columns.py, miller/, and translations/ into
# ~/.local/share/nemo-python/extensions so the repository folder can be
# removed after installation. When launched without a controlling terminal
# (for example, by double-clicking install.sh in Nemo and choosing "Run"),
# re-launches itself inside a terminal window so progress and password prompts
# are visible.
set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
REPO_DIR="$(dirname "$SCRIPT_PATH")"
EXT_DIR="${HOME}/.local/share/nemo-python/extensions"
ENTRY_NAME="nemo_miller_columns.py"
PKG_NAME="miller"
I18N_NAME="translations"

SKIP_DEPS=0
UNINSTALL=0
LINK_MODE=0

usage() {
  cat <<'EOF'
Usage: ./install.sh [--skip-deps] [--link] [--uninstall]

Copy nemo_miller_columns.py, miller/, and translations/ into
~/.local/share/nemo-python/extensions and quit Nemo so it reloads extensions.

  --skip-deps   Do not install system packages
  --link        Symlink to this repository instead of copying files
  --uninstall   Remove the installed extension files and quit Nemo
  -h, --help    Show this help
EOF
}

ORIG_ARGS=("$@")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-deps)
      SKIP_DEPS=1
      ;;
    --link)
      LINK_MODE=1
      ;;
    --uninstall)
      UNINSTALL=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

launch_in_terminal() {
  local runner
  runner="\"$SCRIPT_PATH\""
  for arg in "${ORIG_ARGS[@]}"; do
    runner+=" $(printf '%q' "$arg")"
  done
  runner+="; code=\$?; echo; read -rp 'Press Enter to close...' _; exit \$code"

  if command -v x-terminal-emulator >/dev/null 2>&1; then
    exec x-terminal-emulator -e bash -c "$runner"
  elif command -v gnome-terminal >/dev/null 2>&1; then
    exec gnome-terminal -- bash -c "$runner"
  elif command -v mate-terminal >/dev/null 2>&1; then
    exec mate-terminal -e "bash -c $(printf '%q' "$runner")"
  elif command -v xfce4-terminal >/dev/null 2>&1; then
    exec xfce4-terminal -e "bash -c $(printf '%q' "$runner")"
  elif command -v konsole >/dev/null 2>&1; then
    exec konsole -e bash -c "$runner"
  elif command -v tilix >/dev/null 2>&1; then
    exec tilix -e bash -c "$runner"
  elif command -v kitty >/dev/null 2>&1; then
    exec kitty bash -c "$runner"
  elif command -v alacritty >/dev/null 2>&1; then
    exec alacritty -e bash -c "$runner"
  elif command -v xterm >/dev/null 2>&1; then
    exec xterm -e bash -c "$runner"
  fi
}

# Re-launch in a visible terminal when invoked from a GUI without a tty.
if [[ "${MILLER_IN_TERMINAL:-0}" != "1" ]] && [[ ! -t 0 ]] && [[ ! -t 1 ]] && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  export MILLER_IN_TERMINAL=1
  launch_in_terminal "${ORIG_ARGS[@]}"
fi

remove_installed_files() {
  rm -rf \
    "${EXT_DIR:?}/${ENTRY_NAME}" \
    "${EXT_DIR:?}/${PKG_NAME}" \
    "${EXT_DIR:?}/${I18N_NAME}"
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  echo "Removing Miller Columns from ${EXT_DIR}..."
  remove_installed_files
  echo "Restarting Nemo..."
  nemo -q 2>/dev/null || true
  echo "Miller Columns uninstalled."
  exit 0
fi

deps_installed() {
  command -v nemo >/dev/null 2>&1 &&
    python3 -c "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('Nemo', '3.0'); from gi.repository import Gtk, Nemo" >/dev/null 2>&1
}

install_deps() {
  if command -v apt-get >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1; then
    local missing=()
    for pkg in nemo nemo-python gir1.2-nemo-3.0 python3-gi gir1.2-gtk-3.0; do
      if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        missing+=("$pkg")
      fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
      echo "Installing missing packages (${missing[*]})..."
      sudo apt-get update
      sudo apt-get install -y "${missing[@]}"
    fi
    return
  fi

  if deps_installed; then
    return
  fi

  echo "Installing system dependencies..."
  if command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y nemo nemo-python python3-gobject gtk3
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm nemo nemo-python python-gobject gtk3
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y nemo python-nemo python3-gobject typelib-1_0-Gtk-3_0
  else
    echo "Warning: Could not detect package manager. Ensure nemo, nemo-python, and GTK 3 Python bindings are installed." >&2
  fi
}

echo "=== Miller Columns Installer ==="

if [[ "$SKIP_DEPS" -eq 0 ]]; then
  echo "Checking system dependencies..."
  install_deps
fi

mkdir -p "$EXT_DIR"
remove_installed_files

if [[ "$LINK_MODE" -eq 1 ]]; then
  echo "Linking extension to ${EXT_DIR}/${ENTRY_NAME}..."
  ln -sfn "${REPO_DIR}/${ENTRY_NAME}" "${EXT_DIR}/${ENTRY_NAME}"
else
  echo "Copying extension files to ${EXT_DIR}..."
  cp -f "${REPO_DIR}/${ENTRY_NAME}" "${EXT_DIR}/${ENTRY_NAME}"
  cp -r "${REPO_DIR}/${PKG_NAME}" "${EXT_DIR}/${PKG_NAME}"
  if [[ -d "${REPO_DIR}/${I18N_NAME}" ]]; then
    cp -r "${REPO_DIR}/${I18N_NAME}" "${EXT_DIR}/${I18N_NAME}"
  fi
  find "${EXT_DIR}/${PKG_NAME}" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
fi

echo "Reloading Nemo..."
nemo -q 2>/dev/null || true

echo "Done! Miller Columns is installed."
if [[ "$LINK_MODE" -eq 0 ]]; then
  echo "You can now safely delete this downloaded folder if you wish."
fi
