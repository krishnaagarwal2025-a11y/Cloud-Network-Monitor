# Live acceptance follow-up — 22 October 2026

The user ran the full live acceptance for source revision `53b6c48`. The agent
inspected `results/acceptance_1792648065134307579/acceptance.json`, its test log
and the failing Phase 6 log. This is fresh live evidence, not recorded playback.

## Observed result before the fix

- All 100 tests passed with no skips, including both live Mininet tests.
- Prometheus config, setup shell syntax and whitespace checks passed.
- Phase 3 traffic acceptance passed.
- Phase 4 live ingestion, application outage and recovery passed.
- Phase 5 live Grafana panel/data-path acceptance passed.
- Phase 6 baseline passed with zero anomalies and healthy localization.
- Phase 6 stopped at the first client-edge delay application/rollback, before
  a fault measurement was saved. The final end-to-end experiment was not started.

## Actual failure and targeted fix

The kernel emitted:

```text
Warning: sch_htb: quantum of class 50001 is big. Consider r2q change.
```

Mininet includes this text in `TCIntf.config()['tcoutputs']`, also printing an
`*** Error:` prefix in its log. The project's helper rejected every nonempty
output, so it incorrectly treated this advisory as a configuration failure.
The same check rejected restoration and masked the initial application error.

The fix recognizes only the exact HTB advisory, including its class identifier.
A separate error in the same output block still fails, and unknown warnings
still fail. Before accepting this advisory, the helper reads actual qdisc/class
configuration and checks requested delay, loss, rate and queue size. Missing
hierarchies and stale values are errors. The topology, shaping parameters and
kernel scheduling settings have not been changed to suppress the warning.

Six regression tests cover matching readback, mixed warning/error output, other
warnings, mismatched requested parameters, missing qdiscs and complete owned
apply/restore lifecycle. Existing rollback/error assertions remain intact.

## Post-fix execution

The targeted fault-injection tests and existing audit regressions passed.
The full suite ran 106 tests: 104 passed, 2 root-only tests skipped.
All six portable acceptance stages passed, including the complete 31-cycle
synthetic experiment using actual localhost monitoring services.
Full portable acceptance results are in the companion curated evidence and
local acceptance directory. This does not establish post-fix kernel acceptance.

An attempted affected live rerun, `sudo -n python3 scripts/verify_phase6.py`,
failed before Python started: `sudo: interactive authentication is required`.
No project-named interfaces remained visible in the root namespace at inspection;
OVS database and complete namespace cleanup were not reverified without root.
No blanket cleanup was performed.

## Next live acceptance

From the Ubuntu terminal:

```bash
cd /home/krishna/cloud-network-monitor
sudo python3 scripts/verify_all.py --live
```

This runs the post-fix suite, fault experiment and final demonstration. A
successful Phase 3–5 result from the earlier run must not be misrepresented as
successful post-fix Phase 6 or end-to-end acceptance. If another stage fails,
retain its logs and diagnose the exact output rather than weakening assertions.

Reviewed pre-fix live summary: [live-acceptance-2026-10-22.json](evidence/live-acceptance-2026-10-22.json).
