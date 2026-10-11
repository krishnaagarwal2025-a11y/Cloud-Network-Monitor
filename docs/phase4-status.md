> Historical engineering record. For the current audited source and verification
> status, see [audit-2026-10-11.md](audit-2026-10-11.md). Earlier PASS statements
> refer to their saved runs, not live acceptance of the changes in this audit.

# Phase 4 — Prometheus collection

## Live acceptance update: PASSED

The user completed `scripts/verify_phase4.py` with sudo. The agent inspected
`results/phase4_verification_1791724549436935814/verification.json` and its
underlying measurement files. The mode is `live_mininet`, success is true,
all three stages are present and network cleanup succeeded.

| Stage | HTTP response codes | HTTP probe success | Exporter UP |
| --- | --- | ---: | ---: |
| Baseline | 200, 200, 200 | 1 | 1 |
| Application stopped | 0, 0, 0 | 0 | 1 |
| Recovery | 200, 200, 200 | 1 | 1 |

Prometheus omitted HTTP latency during the outage. Network probes continued
successfully, and HTTP availability recovered after the owned web service was
restarted. Phase 4 is complete. The original implementation history follows.


## Status on 11 October 2026

Implemented and verified through real Prometheus ingestion of a genuine saved
Phase 3 report. Continuous Mininet collection, application-down reporting and
recovery acceptance are implemented but awaiting an authenticated root run.
Phase 4 is not yet marked complete; Phase 5 has not started.

## Components

- `src/monitoring/exporter.py`: a custom collector with atomic snapshots,
  finite numeric validation, bounded host/probe labels and loopback HTTP serving.
- `src/monitoring/prometheus.py`: owned Prometheus subprocess with its own
  configuration, logs, data directory, short retention and graceful teardown.
- `scripts/run_monitoring.py`: continuous genuine measurements using the
  existing topology and TrafficManager; unique JSON files for each cycle.
- `scripts/verify_phase4.py`: live baseline, owned HTTP-service outage and
  recovery; verifies exact values through Prometheus's query API.
- `tests/test_monitoring.py`: metric units, failure semantics, missing values,
  snapshot replacement, label validation, HTTP serving and occupied-port safety.
- `config/prometheus.yml`: exporter scrape target now uses localhost port 9200.
- `src/traffic/manager.py`: targeted methods to stop/restore only its owned HTTP
  child for the application-health acceptance check.

The installed Prometheus 2.53.5 and prometheus-client 0.21.1 were reused. No new
packages were installed. System services on 9090 and 9100 were left unchanged.
The normal project runner uses exporter 9200 and Prometheus 9091. Verification
uses automatically selected loopback ports to avoid existing listeners.
Each owned Prometheus instance writes its own configuration with a one-second
scrape interval; the checked-in standalone example uses two seconds.

## Metric contract

All metric values represent the most recently completed collection cycle.
Labels are bounded to the seven known hosts and three probe types. Timestamps,
URLs, error strings and request IDs are never used as labels.

| Metric | Units / meaning |
| --- | --- |
| `cloud_ping_latency_seconds` | Mean round-trip time, by source/target |
| `cloud_ping_min_seconds`, `cloud_ping_max_seconds` | Minimum/maximum RTT |
| `cloud_ping_variation_seconds` | RTT standard deviation, not one-way jitter |
| `cloud_packet_loss_ratio` | Lost packets / sent packets, 0 to 1 |
| `cloud_http_response_seconds` | Mean HTTP total time; absent if any request failed |
| `cloud_http_connect_seconds` | Mean connect time; absent if any request failed |
| `cloud_http_success_ratio` | Successful requests / requests in the cycle |
| `cloud_http_status_code` | Last HTTP response code; zero if unavailable |
| `cloud_throughput_bits_per_second` | TCP receiver throughput |
| `cloud_tcp_retransmissions` | Retransmissions during the last test, a gauge |
| `cloud_tcp_rtt_seconds` | Optional sender RTT; absent if not provided |
| `cloud_throughput_duration_seconds` | Duration of last throughput test |
| `cloud_probe_success` | Probe health by probe/source/target |
| `cloud_probe_timestamp_seconds` | Original measurement completion time |
| `cloud_collection_success` | Collection completed, even when probes report failures |
| `cloud_collection_timestamp_seconds` | Time the latest collection/import attempt completed |
| `cloud_collection_duration_seconds` | Collection wall time |
| `cloud_collection_errors_total` | Collection exceptions since process start |
| `cloud_measurement_cycle_success` | All baseline probe checks passed |

Prometheus's own `up` metric describes scrape reachability. It must not be
interpreted as application health. An HTTP outage should produce `up=1` while
`cloud_probe_success{probe="http"}=0`. Missing latency/throughput is omitted,
not converted into a healthy zero. An observed 100% loss remains a ratio of 1.
Old path values disappear after the snapshot changes.

Freshness must be checked separately, for example:

```promql
time() - cloud_probe_timestamp_seconds
```

Scrape timestamps describe when Prometheus read the endpoint. The probe-time
metric describes when traffic was actually measured. Loading an old report
must not make old measurements appear newly measured.

## Executed verification

- Updated suite: **47 tests, 45 passed, two root-only tests skipped**.
- `promtool check config config/prometheus.yml`: passed.
- Actual unprivileged integration command:

```bash
python3 scripts/verify_phase4.py --recorded-report results/phase3_verification_1791722428619401825/traffic_metrics_1791722469861611128_e75c3991.json
```

It started a genuine exporter HTTP endpoint and an isolated Prometheus process,
scraped successfully and queried actual measurement values. Evidence:
`results/phase4_verification_1791724358901734947/verification.json`.
Its mode is explicitly `recorded_measurement_ingestion`; no new Mininet traffic
was generated by this check. Original measurement timestamps were preserved.
The report's false network-cleanup field means no network was started in this
mode, not that network cleanup failed.

Verified target health was `up`, with no scrape error. Returned values included:

| Query | Observed value |
| --- | ---: |
| `up{job="cloud-network-monitor"}` | 1 |
| Client → web ping latency | 0.017070000000000002 seconds |
| Client → web packet loss ratio | 0 |
| Client → web throughput | 93,410,000 bits/second |
| Client → web HTTP response | 0.0362478 seconds |

The HTTP value is the mean of the individual recorded requests. It preserves
more precision than the rounded millisecond summary in the Phase 3 report.
The verifier stopped its exporter and Prometheus when finished.

## Remaining acceptance check

The attempted command `sudo -n python3 scripts/verify_phase4.py` failed before
Python started: `sudo: interactive authentication is required`.

Run in the Ubuntu terminal:

```bash
cd /home/krishna/cloud-network-monitor
sudo python3 scripts/verify_phase4.py
```

It creates the project network and services, verifies baseline measurements in
Prometheus, stops only its owned HTTP service, verifies scrape health remains
UP while HTTP fails, restores HTTP, verifies recovery and stops owned resources.
Evidence is saved in a unique `results/phase4_verification_<time>/` directory.
On success, inspect that its mode is `live_mininet`, all three stages are
present and `network_cleanup_success` is true. Then advance to Phase 5.

## Design references

The implementation uses the installed Python client's
[custom collector interface](https://prometheus.github.io/client_python/collector/custom/)
and [HTTP server lifecycle](https://prometheus.github.io/client_python/exporting/http/).
Metric freshness and label choices follow Prometheus's
[instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/).
Active traffic is scheduled separately from scrapes so scrape frequency does
not multiply throughput-test load; scrapes only read the latest complete snapshot.
