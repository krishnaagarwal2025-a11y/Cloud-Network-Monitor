#!/usr/bin/env bash
set -e

echo "=========================================================="
echo " Cloud Application Network Monitoring - Environment Setup"
echo "=========================================================="

echo "[1/4] Updating APT package repositories..."
sudo apt update

echo "[2/4] Installing networking & monitoring packages..."
sudo apt install -y mininet openvswitch-switch iperf3 python3.14-venv python3-pip prometheus

echo "[3/4] Ensuring Open vSwitch service is active..."
sudo systemctl enable --now openvswitch-switch || true

echo "[4/4] Setting up Python virtual environment..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "=========================================================="
echo " Setup complete! Next steps:"
echo " 1. Run Mininet sanity test: sudo mn --test pingall"
echo " 2. Activate venv: source .venv/bin/activate"
echo "=========================================================="
