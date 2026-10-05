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
│   ├── traffic/            # Traffic generation (ICMP, TCP, HTTP) & tc/netem fault injection
│   ├── monitoring/         # Metric collection agents and Prometheus exporter
│   └── analysis/           # Threshold, EWMA, Z-score, and Isolation Forest detectors
├── scripts/                # Execution and automation scripts
│   ├── setup_env.sh        # Dependency installer and environment initializer
│   ├── run_experiment.py   # Repeatable experiment execution runner
│   └── start_monitoring.sh # Monitoring stack management
└── tests/                  # Verification and unit tests
    └── test_topology.py    # Topology structural & live integration tests
```

---

## Network Topology Architecture

The testbed models a production multi-tier cloud architecture with distinct, measurable network segments:

```text
       [ client ] (10.0.0.10)
           │
      ( s_client )
           │
           │  <-- seg_client_edge: 100 Mbps, 5ms delay (WAN access)
           │
       ( s_edge )
           │
      [ edge_gw ] (10.0.0.1)
           │
           │  <-- seg_edge_lb: 1 Gbps, 2ms delay (Perimeter / DMZ)
           │
        ( s_lb )
           │
        [ lb ] (10.0.0.2)
           │
           │  <-- seg_lb_app: 1 Gbps, 1ms delay (Service Mesh)
           │
       ( s_app )
      ┌────┼────┐
      │    │    │
  [web_srv] [api_srv] [auth_srv]  (10.0.0.11 / 12 / 13)
      └────┬────┘
           │
           │  <-- seg_app_db: 1 Gbps, 1ms delay (Data Tier backend)
           │
        ( s_db )
           │
       [ db_srv ] (10.0.0.20)
```

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

### 3. Cleaning Up Mininet

If Mininet or Open vSwitch ever terminates unexpectedly or interfaces remain:

```bash
sudo mn -c
```

---

## Phase 3: Traffic Generation & KPI Measurement

Phase 3 introduces a lightweight measurement and probing layer on top of the cloud topology without requiring external agents or daemons:

### 1. Probing & Measurement Capabilities

- **ICMP Monitoring (`src/traffic/icmp.py`)**:
  - Probes key paths (`client -> edge_gw`, `client -> web_srv`, `client -> api_srv`, `client -> db_srv`, `web_srv -> db_srv`).
  - Measures `rtt_min_ms`, `rtt_avg_ms`, `rtt_max_ms`, `rtt_mdev_ms` (jitter), and `packet_loss_percent`.
- **HTTP Monitoring (`src/traffic/http.py`)**:
  - Manages background HTTP service on `web_srv:8080`.
  - Probes HTTP response time, TCP connect time, and status codes using `curl`.
  - Records `http_response_time_ms`, `http_status`, and `http_success`.
- **Throughput Testing (`src/traffic/iperf.py`)**:
  - Manages `iperf3` servers on `web_srv:5201` and `db_srv:5201`.
  - Directly executes `iperf3 -c <ip> -J` to capture precise machine-readable JSON metrics (`throughput_mbps`, `retransmits`, `mean_rtt_ms`).
- **Orchestration & Structured Metrics (`src/traffic/manager.py`)**:
  - `TrafficManager` coordinates lifecycle, executes test suites, and outputs structured dictionary/JSON ready for Prometheus exporters.

### 2. How to Run Phase 3

#### Run Unit & Integration Tests:
```bash
# Unit parsing tests (runs unprivileged):
python3 -m unittest discover tests

# Live Mininet traffic integration test (requires sudo):
sudo python3 tests/test_traffic.py
```

#### Run Live Traffic Experiment & KPI Measurement:
```bash
sudo python3 scripts/run_traffic_test.py
```

### 3. Example Measurement Output

```text
======================================================================
 Cloud Application Network Monitoring - Phase 3 Traffic & KPIs
======================================================================

[1/5] Instantiating and starting Mininet cloud topology...
  ✓ Topology started successfully (7 hosts, 5 OVSBridge switches)

[2/5] Setting up background application services...
  ✓ HTTP server running on web_srv:8080
  ✓ iperf3 servers running on web_srv:5201 and db_srv:5201

[3/5] Measuring ICMP Ping Latency & Packet Loss...
Path               | Target IP | Packets | Loss | Avg RTT | Min RTT | Max RTT | Jitter  | Status
-------------------+-----------+---------+------+---------+---------+---------+---------+-------
client -> edge_gw  | 10.0.0.1  | 3/3     | 0.0% | 5.21 ms | 5.12 ms | 5.34 ms | 0.09 ms | PASS  
client -> web_srv  | 10.0.0.11 | 3/3     | 0.0% | 8.35 ms | 8.21 ms | 8.52 ms | 0.13 ms | PASS  
client -> api_srv  | 10.0.0.12 | 3/3     | 0.0% | 8.31 ms | 8.19 ms | 8.48 ms | 0.12 ms | PASS  
client -> db_srv   | 10.0.0.20 | 3/3     | 0.0% | 9.42 ms | 9.30 ms | 9.61 ms | 0.14 ms | PASS  
web_srv -> db_srv  | 10.0.0.20 | 3/3     | 0.0% | 1.15 ms | 1.08 ms | 1.22 ms | 0.06 ms | PASS  

[4/5] Measuring HTTP Response Time (client -> web_srv:8080)...
Request  | Source -> Target | URL                     | Status Code | Connect Time | Total Time | Result
---------+------------------+-------------------------+-------------+--------------+------------+-------
Req #1   | client -> web_srv| http://10.0.0.11:8080/  | 200         | 8.24 ms      | 15.60 ms   | 200 OK
Req #2   | client -> web_srv| http://10.0.0.11:8080/  | 200         | 8.18 ms      | 14.85 ms   | 200 OK
Req #3   | client -> web_srv| http://10.0.0.11:8080/  | 200         | 8.12 ms      | 14.72 ms   | 200 OK
Req #4   | client -> web_srv| http://10.0.0.11:8080/  | 200         | 8.20 ms      | 14.90 ms   | 200 OK
Req #5   | client -> web_srv| http://10.0.0.11:8080/  | 200         | 8.15 ms      | 14.65 ms   | 200 OK

[5/5] Measuring TCP Throughput with iperf3...
Path              | Target IP | Duration | Throughput  | Retransmits | Mean RTT | Status
------------------+-----------+----------+-------------+-------------+----------+-------
client -> web_srv | 10.0.0.11 | 3.0 s    | 98.40 Mbps  | 0           | 8.25 ms  | PASS  
client -> db_srv  | 10.0.0.20 | 3.0 s    | 98.25 Mbps  | 0           | 9.35 ms  | PASS  

======================================================================
 Summary KPI Metrics (Ready for Prometheus Telemetry Exporter)
======================================================================
  • ping_latency_ms:        8.35 ms
  • packet_loss_percent:    0.0 %
  • http_response_time_ms:  14.94 ms
  • http_success:           1
  • throughput_mbps:        98.40 Mbps
======================================================================

✓ Saved structured metrics to: results/traffic_metrics_latest.json
```

