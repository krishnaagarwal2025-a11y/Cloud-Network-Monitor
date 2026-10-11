# Project engineering instructions

Academic Mininet testbed: seven hosts, five controllerless OVS bridges, eleven
TCLinks. Preserve the topology and active-probe → exporter → Prometheus → Grafana
architecture. Named tiers represent L2 attachments, not implemented gateway,
load-balancer, authentication or database applications.

- Inspect Git status/diffs first and preserve existing work. Never reset, clean,
  force-push, change remotes, auto-commit or auto-publish.
- Entry points: `scripts/run_monitoring.py`, `scripts/run_traffic_test.py`,
  `src/monitoring/exporter.py`. Source is under `src/`; provisioning under `config/`.
- Tests: `python3 -m unittest discover -v -s tests`. Configuration:
  `promtool check config config/prometheus.yml`; `bash -n scripts/setup_env.sh`.
- Portable simulation: `python3 scripts/verify_phase6.py --simulated`.
- Live Mininet and tc/netem require root, active OVS and no colliding project
  bridges. Explain privileged effects before execution. Never kill unrelated
  listeners or use blanket `mn -c` cleanup. Use owned lifecycle contexts.
- Distinguish units/mocks, synthetic simulation, recorded-data ingestion, live
  service checks and live Mininet. Execute tests before claiming success. Missing
  observations are unknown; localization confidence is a candidate heuristic.
- Keep secrets, `.runtime/`, raw experiment state and `.fullerenes/` local.
  README and `docs/audit-2026-10-11.md` give current verification limitations.
- Fullerenes is optional agent tooling only. Use `bash scripts/fullerenes.sh query`
  for focused structural retrieval; verify retrieved source before editing.
  `bash scripts/fullerenes.sh index .` refreshes after changes and preserves agent
  instructions. Do not run upstream init/index/watch directly: 0.1.4 can erase
  handwritten instruction files. Generated summaries are advisory.
