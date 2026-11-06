#!/usr/bin/env bash
# Link the Miller Columns extension into the per-user nemo-python directory.
#
# The Python file is imported by nemo-python, so it does not need an
# executable bit. Uninstall removes only this symlink. Dashboard files,
# bookmarks, desktop files, and dbus services stay where they are.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="${HOME}/.local/share/nemo-python/extensions"
LINK_NAME="nemo_miller_columns.py"
LINK_PATH="${EXT_DIR}/${LINK_NAME}"
TARGET="${REPO_DIR}/nemo_miller_columns.py"

SKIP_DEPS=0
UNINSTALL=0

usage() {
  cat <<'EOF'
Usage: ./install.sh [--skip-deps] [--uninstall]

Link nemo_miller_columns.py into ~/.local/share/nemo-python/extensions
and quit Nemo so it reloads extensions.

  --skip-deps   Do not install apt packages
  --uninstall   Remove the extension symlink and quit Nemo
  -h, --help    Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-deps)
      SKIP_DEPS=1
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

if [[ "$UNINSTALL" -eq 1 ]]; then
  if [[ -L "$LINK_PATH" ]]; then
    rm -f "$LINK_PATH"
  fi
  nemo -q 2>/dev/null || true
  exit 0
fi

if [[ "$SKIP_DEPS" -eq 0 ]] && command -v dpkg >/dev/null 2>&1; then
  missing=()
  for pkg in nemo nemo-python gir1.2-nemo-3.0 python3-gi gir1.2-gtk-3.0; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
      missing+=("$pkg")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
  fi
fi

mkdir -p "$EXT_DIR"
ln -sfn "$TARGET" "$LINK_PATH"
nemo -q 2>/dev/null || true
