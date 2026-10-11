# Phase 3 engineering record — 11 October 2026

## Live acceptance update: PASSED

The user subsequently ran the privileged verifier. The agent inspected:
`results/phase3_verification_1791722428619401825/verification.json`, the
associated test log, and both experiment reports. All **37 tests passed with
no skips**, and both full experiments returned zero with verified cleanup.
The stored reports also passed independent summary/record consistency checks.

| Run | Client → web RTT (ms) | Loss (%) | HTTP response (ms) | Throughput (Mbps) |
| --- | ---: | ---: | ---: | ---: |
| Experiment 1 | 17.461 | 0 | 36.10 | 93.70 |
| Experiment 2 | 17.070 | 0 | 36.25 | 93.41 |

These are actual saved measurements, not examples. The agent reviewed the
saved execution evidence; it did not itself authenticate the root commands.
An additional user-run traffic report also passed its consistency checks.
Phase 3 is complete, and Phase 4 may proceed. The sections below preserve the
historical implementation and initial authentication blocker.


## Repository and scope

Continued the existing repository on `main`, starting at `a192939`.
The working tree was clean at the start. No commits, dependency installations,
service changes or topology redesign were performed. Changes remain uncommitted.
`src/topology/cloud_topology.py` is unchanged.

## Reliability changes

| Files | Purpose |
| --- | --- |
| `src/traffic/process.py` | Own child handles, bounded probes, Linux socket ownership readiness, TERM/wait/KILL and reaping |
| `src/traffic/http.py` | Dedicated HTTP document directory, exact child cleanup, curl exit status, direct lab probes bypassing proxies |
| `src/traffic/iperf.py` | Independent server children, bounded clients, invalid-schema handling, unavailable statistics as null |
| `src/traffic/icmp.py` | Bounded ping, exit status/diagnostics, missing metrics as null |
| `src/traffic/manager.py` | Startup rollback, all-child teardown attempts, one-cycle summary with explicit baseline success |
| `src/topology/lifecycle.py` | Locking, collision refusal, partial-build teardown, owned node-shell reaping, post-stop bridge/interface checks |
| `scripts/run_traffic_test.py` | Same records for tables and JSON, nonzero failure status, interruption handling, unique evidence files |
| `scripts/verify_phase3.py` | Full privileged tests, two experiments, report/summary and process-exit checks |
| `tests/test_reliability.py` | Real local process regressions plus mocked failure, cleanup and report tests |
| `tests/test_topology.py` | MAC and link QoS assertions; safer live lifecycle |
| `tests/test_traffic.py` | Both live iperf targets, simultaneous service readiness and child-exit assertions |
| `README.md` | Actual architecture, commands, metric semantics, limitations; removed unverified sample values |

The service start helpers now return managed service handles rather than bare
PIDs. Stop helpers require those handles. Internal callers have been updated.
The report path is unique per run; there is no automatically overwritten
`traffic_metrics_latest.json` file. Missing metrics now serialize as `null`,
which future consumers must handle alongside `success` and `exit_code`.

## Verified execution

- Baseline: `python3 -m unittest discover -v -s tests` ran 17 tests:
  **15 passed, two live tests skipped**.
- An intermediate sandbox run failed two local socket tests with
  `PermissionError: [Errno 1] Operation not permitted`. This was an execution
  restriction, not bypassed by weakening assertions. Running outside the
  sandbox as the ordinary user allowed the socket tests to execute.
- Final unprivileged suite: **37 tests, 35 passed, two live tests skipped**.
  Output: `results/phase3_nonroot_20261011.log` (Git-ignored).
- Actual local-process tests verified HTTP 200 responses, two simultaneously
  listening iperf3 servers on the same port/different loopback IPs, survival
  of the other server when one stops, a pre-existing listener surviving a
  failed competing startup, repeated HTTP startup/shutdown, bounded startup
  failure and probe timeout with child reaping.
- Unit tests verified rollback, cleanup after failures, network lifecycle
  failure paths with mocks, failure report exit codes, one collection cycle,
  summary/JSON agreement, valid JSON and preservation of previous reports.
- Structural tests verified host/switch identities, IPs, MACs, DPIDs, link
  count, bandwidth, delay, loss and segment queue settings.
- `git diff --check` passed.

The local-process tests do **not** establish Mininet throughput or network
latency. Mocked network tests do **not** establish live namespace cleanup.
No genuine Mininet measurement report has been produced in this session.

## Host checks and blocker

Observed: Python 3.14.4, Mininet 2.3.0, OVS 3.7.1, iperf3 3.20,
curl 8.18.0 and iproute2/tc 6.19.0. OVS service is active; ovsdb-server and
ovs-vswitchd are running. No iperf3 or mnexec process appeared in the initial
host process check. There was no existing results directory at initial inspection.

Direct OVS database inspection without sudo returned `Permission denied`.
The attempted live acceptance command was:

```bash
sudo -n python3 scripts/verify_phase3.py
```

It returned exit status 1 and:

```text
sudo: interactive authentication is required
```

The Python acceptance script did not start, so no Mininet resources were
created by that attempt. Phase 3 live topology/traffic acceptance remains
**pending**. No blanket Mininet cleanup was run.

## Minimum action to unblock

Run this in your Ubuntu terminal and enter your sudo password there:

```bash
cd /home/krishna/cloud-network-monitor
sudo python3 scripts/verify_phase3.py
```

This starts temporary project networks, generates real traffic and tears down
owned resources. It saves all test output, two experiment reports and an
acceptance summary in a new `results/phase3_verification_<time>/` directory.
Tell the project agent when the command finishes; it can read the saved files
directly, diagnose failures and continue. Do not paste credentials into chat.

## Continuation roadmap

1. Inspect the privileged acceptance evidence and fix any actual failures.
2. Require both live tests and two full experiments to pass, with valid
   measurements and verified cleanup, before marking Phase 3 complete.
3. Phase 4: real exporter, Prometheus target UP and query verification.
4. Phase 5: provisioned Grafana dashboards using those real measurements.
5. Phase 6: configurable thresholds, EWMA and Z-score with evaluation.
6. Phase 7: targeted tc/netem fault injection and restoration/recovery checks.
7. Phase 8: measurement-based segment localisation, ambiguity and evaluation.
8. End-to-end demonstration, genuine evidence, plots and academic documentation.

Do not auto-commit. Keep advancing after verified phases; no new phase prompt
is needed. Large installations, scope changes or disruptive system actions
still require a separate decision. Do not begin Phase 4 while Phase 3 live
acceptance is blocked.
