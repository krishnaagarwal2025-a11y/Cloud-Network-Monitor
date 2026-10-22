# Reproducible university demonstration

Final live acceptance passed on 22 October 2026: 106 tests, no skips, and all
experiment stages. See [final-results.md](final-results.md) for the reviewed
measurements and figures. The commands below reproduce that demonstration.

## Prepare once

Follow the Ubuntu setup in README. Ensure Mininet/OVS, ping/curl/iperf3,
Prometheus, prometheus-client and the pinned local Grafana runtime are available.
Do not run two project networks simultaneously. Inspect colliding resources and
stop their owner normally; never use blanket `mn -c` or kill processes by name/port.

## Final acceptance

```bash
cd /home/krishna/cloud-network-monitor
sudo python3 scripts/verify_all.py --live
```

This one command runs unit/structural/live tests, configuration checks, the phase
acceptance scripts and the complete network-fault demonstration. It creates only
owned temporary network resources, restores link shaping and saves all logs to
`results/acceptance_<time>/`. A zero status and `success: true` in that directory's
`acceptance.json` establish that every included check passed. A failed stage stops
the sequence and leaves diagnostic evidence. Do not claim completion from a log
that skipped or failed a live stage.

For portable software verification, without root:

```bash
python3 scripts/verify_all.py
```

This runs real localhost monitoring services with explicitly synthetic faults.
It does not establish kernel shaping or live network recovery.

## End-to-end demonstration

```bash
sudo python3 scripts/run_demo.py --hold-seconds 180
```

Open the printed localhost dashboard URL. The script selects free ports rather
than changing an existing service. The overview and anomaly dashboards update
from the same real measured cycles. Observe normal operation, delay, loss, core
ambiguity, bandwidth restriction, HTTP failure and recovery. Orange experiment
panels describe ground truth, while anomaly/candidate panels describe inference.
After the finite sequence, the stack stays open for the requested review period
and then shuts down its owned resources. Ctrl+C also follows cleanup paths.

Evidence is saved under `results/end_to_end_<time>/`:

- `experiment.json`: mode, settings, events, cycles, algorithm findings, queries,
  errors, exit status and cleanup evidence; also tracks partial progress.
- `summary.md`: per-detector evaluation, localization and recovery tables.
- `report.html` and four `.svg` files: measured curves and missing-data gaps.
- `traffic_metrics_*.json`: original, timestamped measurements.

For safe practice without Mininet, use `python3 scripts/run_demo.py --simulated`.
Its reports and charts prominently identify simulation. It is suitable for testing
software flow and must not be presented as a new live network experiment.

## Mininet terminal evidence

Run separately after other project networks have stopped:

```bash
sudo python3 -m src.topology.cloud_topology
```

At the `mininet>` prompt run `nodes`, `net`, `pingall`,
`client ping -c 3 10.0.0.11`, and `web_srv ping -c 3 10.0.0.20`. Use `exit` for
normal shutdown. This demonstrates node/link creation and actual connectivity.
It does not run the monitoring stack or attach to a simultaneously running demo.

## Evaluation discussion

The supported claim is a reproducible academic monitoring experiment in a
Mininet-emulated environment. Show a uniquely identifiable edge fault and an
ambiguous core fault. Explain that candidate confidence is a heuristic, that an
application outage is not automatically a network fault, and that bandwidth
restriction can be detected without enough evidence to localize it from ICMP.
Production cloud validation, learned high accuracy and real LB/API/DB applications
are outside the demonstrated scope.
