# Experiment methodology and interpretation

The testbed represents a switched cloud-like network. Seven endpoint namespaces
attach to five controllerless OVS bridges, with four shaped inter-tier segments.
Role names do not implement gateway, proxy, API, authentication or database software.
Measurements come from ping, curl and iperf3, not from cloud-vendor telemetry.

## Reproducible collection

Each collection sequentially measures the five declared ICMP paths, repeated HTTP
requests and two TCP throughput paths. Sequential probing avoids concurrent iperf
load contaminating ping/HTTP in the same collection, but different observations
still happen at different times. A cycle timestamp does not make the probes
simultaneous. Throughput testing actively consumes available capacity.

The exporter publishes an atomic snapshot. Prometheus scraping reads that snapshot
and does not launch additional tests. Probe timestamps expose measurement age;
exporter reachability and application health are separate signals. Missing numbers
remain null/absent, including latency under total loss. Ping mdev is RTT standard
deviation and is not an RFC one-way jitter measurement.

## Detection and calibration

`config/analysis.json` defines path RTT limits, packet loss, HTTP response limits,
throughput floor, EWMA settings, Z-score settings and inference tolerances.
`scripts/run_monitoring.py --analysis-config PATH` and the demo accept an alternate
file. Threshold values are explicit lab assumptions and need calibration on a
new machine. They are not universal service-level objectives.

Six initial baseline cycles warm up statistical detectors. HTTP requests within
a collection are averaged into one statistical observation per path; repeated
requests cannot silently accelerate cycle-based warm-up. Thresholds still inspect
individual failures. EWMA uses a damped update for outliers; Z-score uses a bounded
sliding window with minimum samples and zero-variance protection. Both statistical
methods operate on available numeric values, so a total application failure may
be caught by availability thresholds while producing no statistical timing alert.

## Controlled conditions and kernel evidence

`config/experiment.json` defines baseline, fault and recovery cycle counts and
five conditions: client-edge delay, app-DB total loss, core delay, client-edge
bandwidth restriction and owned HTTP service outage.

Before a network change the runner records the exact segment interfaces and their
qdisc/class output. It verifies baseline HTB/netem delay, loss, rate and queue size.
Fault application is a separate event from the request and is followed by checking
the requested parameters in kernel output on both egress interfaces. After
restoration, the same fields must match the captured baseline. All changes remain
inside the owned Mininet topology. Blanket qdisc deletion and global cleanup are
not used. Failed restoration is an error, not a success message.

Delay replaces the per-direction baseline; 50 ms on a link does not mean exactly
50 ms added to an end-to-end RTT. Total loss is deterministic for the short
acceptance probe count. Random partial loss needs more packets and repetitions.
The existing injector restores the declared topology configuration, so the demo
refuses unexpected pre-existing shaping rather than claiming arbitrary qdisc
restoration.

## Localization and ground truth

The localizer receives observations and measured latency/loss anomalies only.
Requested/injected segment labels are exported separately for the ground-truth
timeline and evaluation. Healthy observed paths eliminate candidate segments;
degraded paths identify candidate intersections. Competing evidence and missing
coverage remain inconclusive. Core segments can share identical signatures.

A single candidate means the heuristic single-fault model selected one segment.
It is not proof of root cause or a calibrated 100% probability. Bandwidth-only
changes can be detected from throughput while remaining unlocalizable with the
ICMP matrix. An HTTP application outage can be detected while network inference
correctly remains healthy. Those limits are demonstrated rather than hidden.

## Evaluation

The report calculates cycle-level TP, FP, TN and FN separately for thresholds,
EWMA and Z-score. Warm-up cycles are excluded. Precision, recall and false-positive
rate are null if their denominators are zero. Detection delay is elapsed time
between the applied event and the first positive observation, including probe
and collection duration. In simulation it describes processing cadence, not
physical response time. The small deterministic experiment does not establish
production accuracy or compare methods over representative internet traffic.

Recovery requires successful probes, healthy network inference, no threshold
breach and primary-path RTT/throughput reasonably close to the measured baseline.
The default tolerances are RTT <= baseline median × 1.5 + 5 ms and throughput
>= baseline median × 0.7. These tolerances are acceptance assumptions, not an
improvement claim. Statistical residual alerts, if any, remain in the evaluation.

## Provenance and figures

Every run has a distinct directory, immutable raw cycle reports, event records,
Prometheus/Grafana query evidence, evaluation tables, SVG plots and a browser-
readable HTML report. The x-axis uses collection index, explicitly avoiding an
assumption of evenly spaced observations. Gaps mean missing measurements.
Yellow bands are controlled-condition labels, not independent anomaly findings.

Modes are explicit: synthetic simulation, historical recorded playback, or live
Mininet. Reviewed historical live graphs under `docs/figures/` retain their
October 11 measurement date. They do not establish live acceptance of later code.
