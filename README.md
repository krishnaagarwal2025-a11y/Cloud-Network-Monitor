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
│   │   ├── __init__.py
│   │   └── cloud_topology.py
│   ├── traffic/            # ICMP, HTTP, iperf3 probes and owned child processes
│   ├── monitoring/         # Empty scaffold; Phase 4 pending
│   └── analysis/           # Empty scaffold; later phases pending
├── scripts/                # Execution and automation scripts
│   ├── setup_env.sh        # Dependency installer and environment initializer
│   ├── run_traffic_test.py # One Phase 3 measurement cycle and JSON report
│   └── verify_phase3.py    # Tests, two live experiments, evidence checks
└── tests/                  # Verification and unit tests
    └── test_topology.py    # Topology structural & live integration tests
```

---

## Network Topology Architecture

The testbed models a production multi-tier cloud architecture with distinct, measurable network segments:

```text
 s_client ---- s_edge ---- s_lb ---- s_app ---- s_db
     |            |          |        |          |
   client       edge_gw       lb       |        db_srv
                              web_srv, api_srv, auth_srv
                              (each attached to s_app)
```

The four switch-to-switch links define the monitored segments. All hosts
share one IPv4 subnet and communicate through OVS learning bridges.
`edge_gw` and `lb` are logical role names: they do not currently implement
routing, NAT, reverse proxying, or load balancing. The HTTP service is a
small availability endpoint, not a complete cloud application.

### Node Addressing & Roles

| Node Name | Type | IP Address | MAC Address | Role / Description |
| :--- | :--- | :--- | :--- | :--- |
| **`client`** | Host | `10.0.0.10/24` | `00:00:00:00:00:10` | External client simulating user traffic |
| **`edge_gw`** | Host | `10.0.0.1/24` | `00:00:00:00:00:01` | Edge / Ingress Internet Gateway |
| **`lb`** | Host | `10.0.0.2/24` | `00:00:00:00:00:02` | Internal Application Load Balancer |
| **`web_srv`** | Host | `10.0.0.11/24` | `00:00:00:00:00:11` | Frontend Web Service |
| **`api_srv`** | Host | `10.0.0.12/24` | `00:00:00:00:00:12` | Backend REST API Service |
| **`auth_srv`** | Host | `10.0.0.13/24` | `00:00:00:00:00:13` | Authentication & Identity Service |
| **`db_srv`** | Host | `10.0.0.20/24` | `00:00:00:00:00:20` | Database Storage Service |
| **`s_client`** | Switch | - | - | DPID: `0000000000000001` (Client Access Switch) |
| **`s_edge`** | Switch | - | - | DPID: `0000000000000002` (Edge Gateway Switch) |
| **`s_lb`** | Switch | - | - | DPID: `0000000000000003` (Load Balancer DMZ Switch) |
| **`s_app`** | Switch | - | - | DPID: `0000000000000004` (App Services Switch) |
| **`s_db`** | Switch | - | - | DPID: `0000000000000005` (Database Tier Switch) |

### Measurable Inter-Tier Segments

Each inter-tier boundary is connected via a dedicated Open vSwitch link with baseline QoS parameters using `TCLink`:

| Segment Identifier | Endpoints | Baseline Bandwidth | Baseline Delay | Loss | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`seg_client_edge`** | `s_client` <-> `s_edge` | 100 Mbps | 5 ms | 0% | Client WAN ingress segment |
| **`seg_edge_lb`** | `s_edge` <-> `s_lb` | 1000 Mbps | 2 ms | 0% | Ingress to Load Balancer DMZ |
| **`seg_lb_app`** | `s_lb` <-> `s_app` | 1000 Mbps | 1 ms | 0% | Load Balancer to App service mesh |
| **`seg_app_db`** | `s_app` <-> `s_db` | 1000 Mbps | 1 ms | 0% | App services to Database backend |

---

## Operating Instructions

### 1. Starting the Topology Interactively

You can launch the topology directly into the Mininet CLI:

```bash
# Method A: Using the standalone script (recommended)
sudo python3 src/topology/cloud_topology.py

# Method B: Using the Mininet CLI with custom topo flag
sudo mn --custom src/topology/cloud_topology.py --topo cloud --switch ovsbr --controller none --link tc
```

Inside the Mininet CLI, test connectivity:
```text
mininet> nodes
mininet> net
mininet> pingall
mininet> client ping -c 3 edge_gw
mininet> exit
```

### 2. Running Topology Tests

Run the test suite to verify graph structure, node attachments, and live connectivity:

```bash
# Structural verification (requires no root privileges):
python3 tests/test_topology.py

# Full live network lifecycle & connectivity test (requires sudo):
sudo python3 tests/test_topology.py
```

The test validates:
- [x] All 7 hosts and 5 switches are created.
- [x] All 11 links (7 host + 4 segment links) exist with expected QoS settings.
- [x] Host deterministic IP configurations match specification.
- [x] Tier-to-tier reachability and 0% packet loss on `pingall`.
- [x] Clean network teardown.

### 3. Network ownership and cleanup

The Phase 3 runner and live tests use `src/topology/lifecycle.py` around the
existing topology. It locks concurrent project runs, refuses conflicting
project-named interfaces/bridges, and stops the network it created in a
`finally` block. It then checks that project-named root-namespace interfaces
and OVS bridges are gone. Partial network construction also has a teardown
path. These privileged paths passed the Phase 3 acceptance run on the host.

Do not run broad process cleanup or `mn -c` automatically on a shared computer.
If startup reports a conflict, inspect the named resource before deciding
whether it belongs to an earlier project run. Forced termination (SIGKILL),
a machine crash, or an OVS failure can still require manual recovery.

---

## Phase 3: Traffic generation and KPI measurement

### Implementation

- ICMP probes cover client to edge, web, API and DB, plus web to DB.
- HTTP probes use curl against a dedicated page served on `web_srv:8080`.
  Proxy environment variables are bypassed for these lab probes.
- iperf3 servers run on both `web_srv:5201` and `db_srv:5201`.
- Services are owned `Popen` children. Readiness checks match the listening
  socket to that child's file descriptors within its network namespace.
  Starting or stopping a server never kills another process by name or port.
- Shutdown sends TERM to each owned child, waits, and uses KILL only on that
  same child if necessary. Probe execution is bounded and children are reaped
  on timeout or interruption.
- The runner collects one measurement cycle. Tables, summary and saved JSON
  all derive from that cycle. A baseline run fails on missing/failed probes,
  any measured packet loss, HTTP status other than 200, or invalid throughput.
- Missing RTT, throughput and optional iperf statistics are JSON `null`.
  Every probe includes success, exit status, raw output and diagnostic fields.
  A measured 100% packet loss remains 100%; a command that produced no loss
  statistics reports `null`. HTTP attempt timings can exist even on failure;
  the summary response time is unavailable if any HTTP request failed.
- Ping `mdev` is RTT standard deviation, used here as latency variation. It
  is not an inter-packet-delay jitter measurement.

The topology's configured delays apply in both travel directions. Historical
README output was illustrative and unverified, and has been removed. No
sample table should be treated as evidence of a live experiment.

### Dependencies

Phase 3 uses Python's standard library plus the installed system Mininet
package, OVS, ping, curl, iperf3 and `tc`. The scientific packages listed in
`requirements.txt` are not needed for Phase 3. Do not install that entire
list merely to run these checks. The existing setup script is a broad initial
installer, not a required step on an already configured machine.

### Run the tests and experiments

From the repository directory, run the unprivileged suite first:

```bash
python3 -m unittest discover -v -s tests
```

Two Mininet tests skip without root. Local subprocess regression tests open
loopback sockets and run real HTTP/iperf3 processes; they require socket access
but no sudo. They are distinct from Mininet integration tests.

For the complete Phase 3 acceptance check:

```bash
sudo python3 scripts/verify_phase3.py
```

This creates temporary Mininet interfaces, OVS bridges, HTTP and iperf3
processes, and generates network traffic. It runs all tests with root,
then two full experiments. Each experiment tears down its own resources.
The verifier checks genuine JSON records, summary consistency, child-process
exit and successful cleanup. A failure stops the sequence and retains logs.

Individual commands remain available:

```bash
sudo python3 tests/test_topology.py
sudo python3 tests/test_traffic.py
sudo python3 scripts/run_traffic_test.py
```

Reports are written to unique `results/traffic_metrics_<time>_<id>.json` files,
or the directory selected with `--output-dir`. Previous reports are preserved.
The acceptance command saves logs, both reports and `verification.json` in a
unique `results/phase3_verification_<time>/` directory. Results are Git-ignored.
A zero exit code means all baseline checks and verified cleanup passed;
errors return 1, and interruption returns 130. Failure reports are saved too.

### Current verification status

Phase 3 live acceptance passed: 37 tests with no skips, two complete traffic
experiments, consistent saved measurements and verified cleanup. See
[the Phase 3 engineering record](docs/phase3-status.md) for evidence paths.

## Phase 4: Prometheus collection

The metrics endpoint publishes the latest complete cycle while a single
measurement loop runs probes sequentially. Scrapes do not trigger additional
iperf traffic. Snapshots update atomically, so a scrape cannot mix two cycles.

```bash
sudo python3 scripts/run_monitoring.py
```

This starts the project network, exporter on `127.0.0.1:9200`, and a separate
Prometheus instance on `127.0.0.1:9091`. It uses the existing installed binary,
leaves the system Prometheus service unchanged, and stores its own logs and
time-series data under a unique `results/monitoring_<time>/` directory.
Ctrl+C stops the owned services and network. Use `--cycles 3` for a finite run.
The default five-second interval is idle time *after* a complete collection;
probing adds its own duration. TCP throughput tests actively load the network.

The host already uses ports 9090 and 9100 for system services. The new ports
avoid those conflicts. Phase 4 live baseline, application failure and recovery
acceptance passed; see the evidence in `docs/phase4-status.md`.

```bash
# Full live acceptance: baseline -> owned HTTP service down -> recovery.
sudo python3 scripts/verify_phase4.py

# Optional unprivileged check of a previously saved genuine report:
python3 scripts/verify_phase4.py --recorded-report /absolute/path/to/traffic_metrics_REPORT.json
```

The recorded-report mode verifies ingestion only. It preserves original probe
timestamps and cannot establish continuous live collection or recovery.

[Metric definitions, failure semantics and verification status](docs/phase4-status.md)
are documented separately.


## Phase 5: Grafana dashboards

The dashboard and datasource are provisioned from `config/grafana/`. The
Grafana OSS binary is pinned, checksum-verified and installed inside `.runtime/`.
The runtime and generated data are Git-ignored. Installation changes no system
packages or services. On a new machine, approve the approximately 461 MB download
before running `python3 scripts/install_grafana.py`.

```bash
sudo python3 scripts/run_monitoring.py --grafana
```

Open `http://127.0.0.1:3000/d/cloud-network-overview`. This localhost instance
provides anonymous read-only viewing, uses a generated admin password, and
loads its datasource and dashboard automatically. Stop with Ctrl+C. Port
conflicts fail without terminating the existing listener.

The dashboard includes exporter reachability, HTTP availability, baseline
checks, probe age, path RTT, loss, HTTP timings, throughput, probe availability
timeline, RTT variation, retransmissions and collection status. Missing values
remain gaps. Baseline checks describe current probe results; statistical
anomaly detection and injected-fault timelines are added in later phases.

```bash
sudo python3 scripts/verify_phase5.py
```

This verifies provisioned dashboard queries during real baseline, application
failure and recovery, and saves their returned data. For unprivileged UI/data
verification, `--recorded-directory` can replay a completed Phase 4 evidence
directory. Playback uses genuine saved records with their original measurement
timestamps; it must not be presented as newly generated live traffic.

## Phase 6: Fault injection, anomaly detection and segment localisation

Controlled network faults (latency, packet loss, bandwidth restrictions) are
injected using Linux `tc`/`netem` via `FaultInjector` on specific Mininet segment
links. All active impairments are tracked and automatically restored via context
manager guarantees.

### Detection & Localisation
- **Anomaly Detection**: Combines three complementary strategies:
  1. Static / Configurable Thresholds: domain bounds on latency, loss, response time, and throughput.
  2. EWMA (Exponentially Weighted Moving Average): adaptive trend tracking with dynamic mean absolute deviation.
  3. Z-score: sliding-window statistical deviation detection with zero-variance protection.
- **Boolean Network Tomography**: Infers likely faulty segments from end-to-end path measurements using the Axiom of Good Paths (healthy link elimination) and intersection of degraded paths. Topologically indistinguishable segments are honestly reported with ambiguous candidate sets and fractional confidence.
- **Grafana Visualization**: A dedicated *Anomaly Detection & Fault Localisation* dashboard is provisioned at `http://127.0.0.1:3000/d/cloud-anomaly-detection`.

```bash
# Unprivileged test suite (validates detectors, tomography, fault injector logic, and dashboard contracts)
python3 -m unittest discover -v -s tests

# Live Mininet Phase 6 acceptance run (requires root)
sudo python3 scripts/verify_phase6.py
```
