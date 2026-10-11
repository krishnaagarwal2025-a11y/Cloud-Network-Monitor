# Phase 5 — Grafana dashboards

## Implementation

- `config/grafana/dashboards/network-overview.json`: thirteen panels built from
  actual exported metrics, with a five-second refresh and visible probe age.
- `config/grafana/provisioning/`: stable datasource/dashboard identifiers and
  automatic file provisioning; runtime environment supplies local paths/ports.
- `src/monitoring/grafana.py`: owned localhost Grafana, isolated session data,
  anonymous Viewer access and a generated admin password. Telemetry and automatic
  plugin installation are disabled. The process is terminated/reaped on exit.
- `scripts/install_grafana.py`: pinned Grafana OSS 13.2.3 download, official
  SHA256 verification, safe archive extraction and refusal to overwrite an
  existing runtime. User explicitly approved the approximately 461 MB download.
- `scripts/run_monitoring.py --grafana`: start the real measurement loop,
  isolated Prometheus and the provisioned dashboard together.
- `scripts/verify_phase5.py`: query every provisioned panel during baseline,
  application outage and recovery; preserve the returned frames as evidence.
- `tests/test_dashboard.py`: datasource/metric contract and query-error handling.

The project continues to use the existing Mininet topology. It does not install
or modify an operating-system Grafana service. The binary is kept under the
Git-ignored `.runtime/grafana-13.2.3/` directory. Per-run Grafana databases,
logs and plugins reside in the run's Git-ignored `results/` directory.

## Dashboard semantics

The overview displays exporter reachability separately from application
availability. Baseline checks use the current all-probes-success result;
they must not be called a statistical anomaly detector. The availability
timeline shows measured failures and recovery, not injected-fault ground truth.
Anomaly and injection-specific panels will be added alongside their verified
implementations in later phases, rather than showing fabricated zero values.

Latency panels use milliseconds; throughput uses Mbps. Source/target paths
remain visible in legends. Missing values are gaps. Probe age makes recorded
or stalled measurements visible, even when the exporter itself is reachable.

## Commands

After the approved local installation:

```bash
sudo python3 scripts/run_monitoring.py --grafana
```

Open `http://127.0.0.1:3000/d/cloud-network-overview`. No manual datasource setup
or login is required for the local read-only view. Ctrl+C stops owned resources.
The runner has optional port overrides when another application uses 3000.

```bash
sudo python3 scripts/verify_phase5.py
```

The acceptance script uses free localhost ports and stores a new evidence
directory. A successful live result must have mode `live_mininet`, three stage
records with returned dashboard query frames, and verified network cleanup.

An unprivileged rendering/data-path check can instead use:

```bash
python3 scripts/verify_phase5.py --recorded-directory results/phase4_verification_1791724549436935814 --hold-seconds 120
```

This replays genuine saved measurements through the real exporter, Prometheus
and Grafana. It keeps their original probe timestamps. The evidence is marked
`recorded_measurement_playback`, and must not be called new Mininet traffic.

## Verification log

- Phase 4 baseline, application-down and recovery evidence was inspected and
  passed before Phase 5 implementation began.
- Updated non-root suite: 49 tests, 47 passed, two root-only tests skipped.
- Dashboard query/rendering verification is pending installation completion.

## Reproducibility references

Pinned archive and checksum:
[official Grafana OSS 13.2.3 download](https://grafana.com/grafana/download/13.2.3?edition=oss&platform=linux).
File provisioning and environment expansion:
[Grafana provisioning documentation](https://grafana.com/docs/grafana/latest/administration/provisioning/).
