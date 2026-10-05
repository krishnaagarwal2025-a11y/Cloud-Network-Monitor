# Cloud Application Network Monitoring

A Linux & Mininet-based testbed for emulating multi-tier cloud application networks, measuring key performance metrics, injecting controlled network faults (delay, jitter, loss, bandwidth saturation), collecting metrics into Prometheus, visualizing via Grafana, and detecting/localizing network anomalies.

## Project Structure

```text
cloud-network-monitor/
├── README.md               # Project documentation and guide
├── requirements.txt        # Python dependencies (Prometheus client, scikit-learn, etc.)
├── .gitignore              # Ignored files (venv, logs, data)
├── config/                 # Service configurations
│   ├── prometheus.yml      # Prometheus scrape jobs and metric targets
│   └── grafana/            # Grafana datasource and dashboard provisioning
├── src/                    # Source code
│   ├── topology/           # Mininet multi-tier cloud topology definitions
│   ├── traffic/            # Traffic generation (ICMP, TCP, HTTP) & tc/netem fault injection
│   ├── monitoring/         # Metric collection agents and Prometheus exporter
│   └── analysis/           # Threshold, EWMA, Z-score, and Isolation Forest detectors
├── scripts/                # Execution and automation scripts
│   ├── setup_env.sh        # Dependency installer and environment initializer
│   ├── run_experiment.py   # Repeatable experiment execution runner
│   └── start_monitoring.sh # Monitoring stack management
└── tests/                  # Verification and unit tests
```

## Quick Setup

Run the automated setup script to install necessary system packages and set up the Python virtual environment:

```bash
bash scripts/setup_env.sh
```

Or manually install packages:

```bash
# 1. System packages
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf3 python3.14-venv python3-pip prometheus

# 2. Virtual environment & Python dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
