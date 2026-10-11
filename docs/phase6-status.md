> Historical engineering record. For the current audited source and verification
> status, see [audit-2026-10-11.md](audit-2026-10-11.md). Earlier PASS statements
> refer to their saved runs, not live acceptance of the changes in this audit.
> Current live Phase 6 uses deterministic 100% app-DB loss and delay overrides
> per direction; older 25% and +RTT descriptions below are superseded. Cleanup
> is attempted and failures are reported; successful kernel rollback is unverified.

# Phase 6 — Fault Injection, Anomaly Detection & Segment Localisation

## Architecture & Implementation

### 1. Multi-Algorithm Anomaly Detection (`src/analysis/detector.py`)
- **`ThresholdDetector`**: Path-aware static thresholds for round-trip latency, packet loss ratio, HTTP response time, and TCP throughput. Preserves domain knowledge of network tier distance.
- **`EWMADetector`**: Adaptive Exponentially Weighted Moving Average with dynamic Mean Absolute Deviation. Self-calibrates against variance, establishes minimum sample warm-up, and damps updates during severe outliers to avoid metric skew.
- **`ZScoreDetector`**: Sliding-window statistical deviation engine ($Z = \frac{x - \mu}{\sigma}$). Enforces finite sample sizes, bounds history windows with deques, and guards against division-by-zero on zero-variance links using domain $\Delta_{min}$.
- **`CompositeAnomalyDetector`**: Aggregates all three algorithms per cycle, calculating per-metric, per-path, and per-detector anomaly counts.
- **Data Integrity**: Missing values (`None` / failed probes) are never converted to zero latency or zero loss.

### 2. Boolean Network Tomography & Fault Localisation (`src/analysis/localiser.py`)
- **Path Routing Matrix**: Models the multi-tier segment coverage:
  - `client -> edge_gw`: $[S_1]$ (`seg_client_edge`)
  - `client -> web_srv`: $[S_1, S_2, S_3]$ (`seg_client_edge`, `seg_edge_lb`, `seg_lb_app`)
  - `client -> api_srv`: $[S_1, S_2, S_3]$
  - `client -> db_srv`: $[S_1, S_2, S_3, S_4]$
  - `web_srv -> db_srv`: $[S_4]$ (`seg_app_db`)
- **Axiom of Good Paths**: Any segment traversed by a healthy probe path is classified as verified healthy and eliminated from the candidate set.
- **Intersection of Bad Paths**: The failure is deduced from the intersection of segments along degraded paths.
- **Honest Ambiguity Reporting**: Indistinguishable intermediate segments (e.g., $S_2$ vs $S_3$) report an explicit candidate set and fractional confidence (e.g. 0.5 for 2-way tie) rather than guessing.
- **Ground Truth Evaluation**: Evaluates inference against known injected segments (`exact_match`, `ambiguous_contains_target`, `false_positive`, `missed_target`).

### 3. Controlled Fault Injection (`src/traffic/fault_injection.py`)
- Leverages Linux `tc` / `netem` through Mininet link interfaces.
- Supports delay (e.g. `50ms`), packet loss percentage (e.g. `20%`), and bandwidth limits.
- Context manager `inject_fault` guarantees automatic rollback to baseline QoS even upon unhandled exceptions.
- Restores original configuration without destructive global qdisc resets.

### 4. Telemetry & Visualization
- **Prometheus Exporter** (`src/monitoring/exporter.py`):
  - `cloud_anomaly_detected`: Gauge (1/0)
  - `cloud_anomalies_total`: Gauge (count)
  - `cloud_anomaly_by_detector{detector=...}`: Gauge
  - `cloud_fault_detected`: Gauge (1/0)
  - `cloud_fault_candidate{segment=...}`: Gauge (1/0)
  - `cloud_fault_confidence{segment=...}`: Gauge (0.0 to 1.0)
- **Grafana Dashboard** (`config/grafana/dashboards/anomaly-detection.json`):
  - Provisioned automatically under the *Networks Lab* folder.
  - Displays Anomaly Status, Active Anomalies Count, Fault Localisation State, Anomaly Breakdown by Engine, and Candidate Segment Bars with Confidence over time.

---

## Verification & Execution Commands

### Unprivileged Unit Test Suite
```bash
python3 -m unittest discover -v -s tests
```
Validates:
- `tests/test_topology.py`: Topology structure and node configuration.
- `tests/test_traffic.py`: Parsing logic for ping, curl, and iperf3.
- `tests/test_monitoring.py`: Prometheus exporter formatting, units, and missing value safety.
- `tests/test_dashboard.py`: Dashboard provisioning contracts for both Overview and Anomaly Detection dashboards.
- `tests/test_anomaly.py`: Threshold, EWMA, and Z-Score detector accuracy, zero-variance safety, and warm-up suppression.
- `tests/test_localiser.py`: Tomography localisation, edge segment isolation, core ambiguity, and ground-truth validation.
- `tests/test_fault_injection.py`: Fault injection lifecycle, state tracking, and context-manager cleanup guarantees.

### Live Phase 6 Acceptance Suite (requires root)
```bash
sudo python3 scripts/verify_phase6.py
```
Performs live end-to-end verification across:
1. Baseline health cycle (0 anomalies, healthy status).
2. Segment fault injection on `seg_client_edge` (+50ms delay).
3. Tomography localisation and ground-truth validation (`exact_match`).
4. Segment fault injection on `seg_app_db` (25% loss).
5. Tomography localisation and ground-truth validation (`exact_match`).
6. Restoration and verified network cleanup.
