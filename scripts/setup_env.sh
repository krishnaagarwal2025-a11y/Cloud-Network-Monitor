#!/usr/bin/env bash
set -euo pipefail

echo "=========================================================="
echo " Cloud Application Network Monitoring - Environment Setup"
echo "=========================================================="

echo "[1/4] Updating APT package repositories..."
sudo apt update

echo "[2/4] Installing networking & monitoring packages..."
sudo apt install -y mininet openvswitch-switch iperf3 iproute2 iputils-ping curl ethtool python3-venv python3-pip python3-prometheus-client prometheus

echo "[3/4] Ensuring Open vSwitch service is active..."
sudo systemctl start openvswitch-switch

echo "[4/4] Setting up Python virtual environment..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [ ! -d ".venv" ]; then
    # Ubuntu supplies Mininet via apt, not PyPI. Make it visible in the venv.
    python3 -m venv --system-site-packages .venv
fi

source .venv/bin/activate
python -m pip install -r requirements.txt
python -c 'import mininet, prometheus_client'

echo ""
echo "=========================================================="
echo " Setup complete! Next steps:"
echo " 1. Check for existing project bridges before live experiments."
echo " 2. Activate venv: source .venv/bin/activate"
echo "=========================================================="
