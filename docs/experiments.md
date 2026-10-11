# Experiment provenance and reproduction

The network has seven hosts/five OVS learning bridges in one /24. TCLink egress
limits and delays are defined in `src/topology/cloud_topology.py`; both directions
are shaped. HTTP serves a fixed response, and iperf3 actively loads TCP paths.
Sequential collection is not simultaneous multi-path observation. Host CPU load,
kernel, Mininet/OVS/iperf versions and probe durations affect results.

`run_traffic_test.py` uses three pings per path, five HTTP requests and three-second
TCP tests. The continuous runner and Phase 4–6 verifiers generally use three
pings/HTTP requests and two-second TCP tests. Zero loss/full HTTP success plus
valid TCP tests is the baseline success condition; it is not a performance SLA.
Metric units and missing-data semantics are in README and docs/phase4-status.md.

## Modes

- Unit fixtures/mocks: not experiment measurements.
- `simulated_fault_injection`: synthetic measurements and deterministic synthetic
  changes through real monitoring services. No kernel injection is performed.
- `recorded_measurement_ingestion` / `recorded_measurement_playback`: existing
  measurements replayed with original probe timestamps. No new probe traffic.
- `live_mininet`: measurements from actual emulated namespaces. A successful
  saved record is historical evidence, not a new run of current modified code.

## Scenarios

Phase 4/5: baseline, stop only the owned HTTP server, recovery. This is an
application outage and should not be interpreted as a network segment fault.

Phase 6 simulation: baseline, synthetic RTT +50 ms on client-edge paths,
synthetic 25% loss on app-DB paths, synthetic RTT +40 ms on edge-LB paths,
recovery. Throughput is deterministically reduced by the simulator and is not
a physical TCP model.

Current Phase 6 live settings: baseline, client-edge egress delay overridden to
50 ms in both directions, app-DB loss 100% in both directions, edge-LB egress
delay overridden to 40 ms in both directions, recovery. Three-packet random
25% loss cannot reliably be observed; deterministic outage verifies failure
handling without pretending to characterize stochastic-loss performance.

The default probe matrix cannot distinguish edge-LB from LB-app. Candidate
confidence is reciprocal candidate count, not measured localization accuracy.
A baseline/recovery result with no candidate does not establish accuracy across
all possible faults. Configurable detector warm-up requires repeated cycles;
the five-stage verifier alone does not validate every statistical algorithm.

## Evidence handling

Follow README reproduction commands. Every verifier saves `verification.json`
with mode, stage reports, query evidence and error status in a unique directory.
Inspect source records as well as success banners. Cleanup fields describe
resources actually started; simulated mode uses null for Mininet cleanup.
Keep raw logs, Prometheus TSDB and Grafana databases local. Small reviewed,
sanitized evidence can be curated under docs with original provenance and hashes.
Older verification metadata contains absolute paths and requires review if moved.

No graphs were produced in this audit. Any future graph must identify its source
JSON, mode, conditions, units, transformations and generation command. Never
label synthetic curves as live performance, or report numerical improvements
without a measured baseline and comparison.
