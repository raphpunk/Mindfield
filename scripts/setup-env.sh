#!/usr/bin/env bash
# Setup script: creates a local virtualenv and installs Python deps there.
# Usage: ./scripts/setup-env.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
REQ_FILE="$ROOT_DIR/requirements.txt"

echo "Project root: $ROOT_DIR"

# Choose python executable
PYTHON=${PYTHON:-python3}

# Create venv if missing
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment at $VENV_DIR"
  $PYTHON -m venv "$VENV_DIR"
else
  echo "Virtualenv already exists at $VENV_DIR"
fi

# Activate and install
# shellcheck source=/dev/null
. "$VENV_DIR/bin/activate"

echo "Upgrading pip and tooling inside venv..."
python -m pip install --upgrade pip setuptools wheel

if [ -f "$REQ_FILE" ]; then
  echo "Installing from $REQ_FILE"
  pip install -r "$REQ_FILE"
else
  echo "No requirements.txt found; installing default SDR deps"
  pip install numpy pyrtlsdr
fi

echo "Environment prepared. To use it, run:" 
echo "  source $VENV_DIR/bin/activate"
echo "Then run the app:"
echo "  python main_app.py"

echo "NOTE: To allow non-root access to RTL-SDR devices you may need to install the udev rule and add your user to group 'plugdev' as described in POLKIT_RULES.md or udev/52-rtl-sdr.rules"

echo "Done."
