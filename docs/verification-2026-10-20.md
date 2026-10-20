# Verification record — 20 October 2026

## Scope and result

Continued `main` from `a0cd93f` with a clean working tree. The original topology
and active-probe → exporter → Prometheus → Grafana architecture are preserved.
The user explicitly authorized completing the remaining work and pushing the
reviewed changes to the existing GitHub repository. No force-push, remote
change, reset, global cleanup or system dependency installation was performed.

**Implementation and portable acceptance are complete. Fresh live acceptance
of the final source remains blocked by interactive sudo authentication.**
This is not a claim that all live milestones passed. Older real live records
are identified separately from current tests and simulations.

## Milestone matrix

| Milestone | Implementation / verification |
| --- | --- |
| Topology and addressing | Existing seven hosts, five OVS bridges, eleven TCLinks; structural tests pass. Historical live connectivity exists. |
| Genuine ICMP/HTTP/TCP measurements | Existing implementation and parser/process tests; historical live Phase 3/4 evidence retained. |
| Prometheus collection | Real local ingestion/query checks pass; explicit missing values, probe age and measurement provenance. |
| Grafana visualization | Both provisioned dashboards queried against real Prometheus; ground truth, inference and measurement source are distinct. Browser visual review unavailable. |
| Threshold/EWMA/Z-score | Central JSON configuration, per-cycle statistical sampling and regression tests; simulated detection evaluated. |
| Controlled delay/loss/bandwidth | Complete experiment; actual qdisc/class snapshot, exact parameter validation and restoration comparison implemented and unit-tested. Final kernel execution pending. |
| Fault localization | Measurement-only inference, exact edge candidates, ambiguous core candidates and unlocalizable conditions demonstrated in simulation. Final live evaluation pending. |
| End-to-end recovery | 31-cycle simulation with real monitoring services passed; same live path is implemented but requires root acceptance. |
| Reports and reproducibility | Single acceptance command, experiment config, immutable cycle evidence, JSON evaluation, Markdown/HTML report, vector figures and demo procedure. |

## Changes completed

- `config/analysis.json` and `src/analysis/configuration.py` centralize the
  thresholds and statistical/localization settings. Unknown keys/paths and
  invalid values fail before traffic collection.
- Statistical HTTP updates now use one aggregate per path/cycle. Repeated
  requests cannot artificially satisfy the warm-up sample count.
- `FaultInjector.snapshot_segment` records both interfaces' qdisc/class output;
  `verify_segment` checks actual HTB/netem delay, loss, rate and queue size.
- `scripts/run_demo.py` combines the existing components in a reproducible,
  finite experiment with warm-up, five conditions and per-condition recovery.
  Kernel snapshots must match before and after restoration in live mode.
- `src/analysis/evaluation.py` computes detector confusion counts, precision,
  recall, false-positive rate, first-detection delay and localization outcomes.
  Missing denominators remain unknown. Synthetic timing is not physical delay.
- `src/analysis/reporting.py` creates tables, HTML and four SVG charts without
  extra dependencies. Missing points are gaps, and the source mode is visible.
- The exporter exposes bounded condition/injection ground truth separately from
  candidate inference. A provenance panel distinguishes live, synthetic and
  recorded/unspecified reports. Historical file imports cannot claim fresh live
  provenance even if their original run was live.
- Existing Grafana reuse now validates both datasource URL and provisioning.
- `scripts/verify_all.py` runs the appropriate full sequence with stage logs,
  bounded subprocess lifetime, fail-fast status and isolated output directories.

## Executed checks and provenance

At initial inspection, 86 tests ran: 84 passed and two root-only tests skipped.
The existing Phase 6 synthetic pipeline passed outside the socket-restricted
sandbox. An initial sandbox attempt failed with `Operation not permitted` and
was rerun with normal host socket access; assertions were not weakened.

The first complete new demo passed all 31 cycles with real localhost exporter,
Prometheus and Grafana. A portable acceptance run passed its six stages. Later
regression checks correctly found that the new ground-truth panels had no data
when their test supplied only plain measurements. The controlled fixture and
Phase 5 playback now explicitly declare the experiment stage; all existing
panel-value assertions remain. A separate test verifies that ordinary reports
do not fabricate fault ground truth.

The final suite ran **100 tests: 98 passed, 2 root-only tests skipped**.
All six portable acceptance stages returned zero, including the 31-cycle
end-to-end simulation with actual exporter/Prometheus/Grafana services.

Definitive post-fix logs and counts are recorded in the curated evidence file
`docs/evidence/portable-acceptance-2026-10-20.json` and the final local acceptance
run. Raw measurements, logs and service databases remain local/Git-ignored.

In the deterministic simulation, threshold detection caught all ten fault
cycles, EWMA caught eight and Z-score caught five, with no false positives in
sixteen evaluated healthy/recovery cycles. These are **synthetic test results**,
not production accuracy claims. Statistical methods did not detect the
application failure from missing timings, and the report retains those misses.
The bandwidth fault was detected but its segment was not identified by ICMP;
core delay produced the two expected indistinguishable candidates.

Reviewed historical live Phase 4 measurements are curated under
`docs/evidence/historical-live-phase4.json`. The two plots in `docs/figures/`
use only those actual records and clearly state their 11 October measurement
date. They do not establish kernel acceptance of the final source.

Prometheus configuration and setup shell syntax are checked by the acceptance
command. Generated SVGs parse as XML. New and tracked text was checked for
private-key, GitHub-token and AWS-access-key patterns; no matches were found.
No raw results, runtime binaries or Fullerenes indexes are tracked.

## Actual unresolved environmental limits

The final live command was attempted:

```bash
sudo -n python3 scripts/verify_all.py --live
```

The operating system returned exit status 1 before Python started:

```text
sudo: interactive authentication is required
```

OVS was active. No project listener on 9200/9091/3000 was present in the initial
host check. Root database inspection was likewise blocked. The agent did not
change sudo policy, bypass authentication, remove foreign resources or claim
new kernel fault/recovery results.

The computer-use tool reported no available apps/browsers, and opening its
in-app browser returned `Browser is not available: iab`. Consequently, Grafana
panel API/data checks are verified, while rendered browser layout remains
unreviewed. The final SVG files were syntax-checked, not visually rasterized.

## One remaining acceptance action

Run this in an Ubuntu terminal and authenticate there:

```bash
cd /home/krishna/cloud-network-monitor
sudo python3 scripts/verify_all.py --live
```

It performs unit/structural/live tests, configuration checks, Phase 3–6 acceptance
and the complete live experiment in order. Each stage owns its resources and
attempts targeted cleanup. Inspect the generated acceptance result and any
failed stage log. Full live completion requires all stages to return zero and
final kernel/service cleanup to succeed; a simulated PASS is insufficient.

The complete methodology and demonstration procedure are in
[methodology.md](methodology.md) and [demo-guide.md](demo-guide.md).
