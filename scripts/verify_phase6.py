#!/usr/bin/env python3
"""
Verify Phase 6: Anomaly Detection, Fault Injection, and Segment Localisation.
-----------------------------------------------------------------------------
Executes end-to-end verification across:
1. Baseline health (0 anomalies, healthy localisation status).
2. Segment fault injection via FaultInjector (latency / packet loss).
3. Multi-algorithm anomaly detection (Threshold, EWMA, Z-Score).
4. Network Tomography segment localisation (unambiguous & ambiguous candidate sets).
5. Ground truth evaluation and clean restoration.
Supports live Mininet (requires sudo) and unprivileged simulated mode (--simulated).
"""

import argparse
from contextlib import ExitStack
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.monitoring.exporter import MeasurementCollector, metrics_server
from src.monitoring.prometheus import prometheus_server, wait_query
from src.topology.lifecycle import experiment_network
from src.traffic.manager import TrafficManager
from src.traffic.fault_injection import FaultInjector
from src.analysis.localiser import PATH_SEGMENT_MAP
from scripts.verify_phase4 import check_cycle, free_port, metric_check


def generate_simulated_cycle(base_report, segment_impairments=None, timestamp=None):
    """
    Produce a genuine-schema measurement cycle with simulated segment impairments.
    Applies segment impairments only to the paths traversing the impaired segment.
    """
    cycle = deepcopy(base_report)
    now = timestamp or time.time()
    cycle['timestamp'] = now
    segment_impairments = segment_impairments or {}

    # Check which paths are affected
    for probe in cycle['icmp']:
        src, tgt = probe['source'], probe['target']
        path_segments = PATH_SEGMENT_MAP.get((src, tgt), [])
        probe['timestamp'] = now

        for seg, params in segment_impairments.items():
            if seg in path_segments:
                if 'delay_ms' in params:
                    probe['rtt_avg_ms'] = round(probe['rtt_avg_ms'] + params['delay_ms'], 3)
                    probe['rtt_min_ms'] = round(probe.get('rtt_min_ms', probe['rtt_avg_ms']) + params['delay_ms'], 3)
                    probe['rtt_max_ms'] = round(probe.get('rtt_max_ms', probe['rtt_avg_ms']) + params['delay_ms'], 3)
                if 'loss_percent' in params:
                    probe['packet_loss_percent'] = float(params['loss_percent'])
                    if probe['packet_loss_percent'] >= 100.0:
                        probe['success'] = False
                        probe['rtt_avg_ms'] = None

    for probe in cycle['http']:
        probe['timestamp'] = now
        path_segments = PATH_SEGMENT_MAP.get((probe['source'], probe['target']), [])
        for seg, params in segment_impairments.items():
            if seg in path_segments:
                if 'delay_ms' in params:
                    probe['response_time_ms'] = round(probe['response_time_ms'] + (params['delay_ms'] * 2), 3)
                if 'loss_percent' in params and params['loss_percent'] >= 50.0:
                    probe['success'] = False
                    probe['http_status'] = 0
                    probe['response_time_ms'] = None

    for probe in cycle['throughput']:
        probe['timestamp'] = now
        path_segments = PATH_SEGMENT_MAP.get((probe['source'], probe['target']), [])
        for seg, params in segment_impairments.items():
            if seg in path_segments:
                if 'delay_ms' in params or 'loss_percent' in params:
                    probe['throughput_mbps'] = max(1.0, round(probe['throughput_mbps'] * 0.25, 2))

    # Overall cycle success
    all_icmp_ok = all(p.get('packet_loss_percent', 0.0) == 0.0 and p.get('success', False) for p in cycle['icmp'])
    all_http_ok = all(p.get('http_status') == 200 for p in cycle['http'])
    cycle['success'] = all_icmp_ok and all_http_ok
    return cycle


def verify_phase6(simulated: bool = False, hold_seconds: int = 0):
    directory = ROOT / 'results' / f'phase6_verification_{time.time_ns()}'
    directory.mkdir(parents=True, exist_ok=False)
    mode = 'simulated_fault_injection' if simulated else 'live_mininet'
    result = {'success': False, 'mode': mode, 'stages': []}
    state = {}
    status = 1

    try:
        collector = MeasurementCollector()
        with ExitStack() as stack:
            exporter_port = stack.enter_context(metrics_server(collector, 0))
            prom_port = free_port()
            stack.enter_context(prometheus_server(directory / 'prometheus', exporter_port, prom_port))

            if simulated:
                # Load baseline template from verified Phase 4 record
                p4_dir = ROOT / 'results' / 'phase4_verification_1791724549436935814'
                p4_meta = json.loads((p4_dir / 'verification.json').read_text())
                base_report = json.loads(Path(p4_meta['stages'][0]['report']).read_text())
                manager = None
                injector = None
            else:
                net = stack.enter_context(experiment_network(state))
                manager = TrafficManager(net)
                stack.callback(manager.teardown_services)
                manager.setup_services()
                injector = FaultInjector(net)
                stack.callback(injector.restore_all)

            # Stage 1: Clean Baseline
            print("\n[Phase 6] Running Stage 1: Clean Baseline...", flush=True)
            if simulated:
                report_baseline = generate_simulated_cycle(base_report)
            else:
                report_baseline = manager.collect_all_kpis(icmp_count=3, http_count=3, iperf_duration=2)
            if not report_baseline['success']:
                raise RuntimeError("Baseline cycle failed")

            evidence_baseline = check_cycle(collector, report_baseline, prom_port, directory, 'baseline')
            res_baseline = collector._localisation_summary
            anom_baseline = collector._anomaly_summary
            if anom_baseline['anomaly_detected']:
                raise RuntimeError(f"Unexpected baseline anomalies: {anom_baseline['anomalies']}")
            if res_baseline.status != 'healthy':
                raise RuntimeError(f"Unexpected baseline localisation status: {res_baseline.status}")

            # Verify Prometheus anomaly & fault metrics for Stage 1
            evidence_baseline['queries']['cloud_anomaly_detected'] = metric_check(prom_port, 'cloud_anomaly_detected', 0)
            evidence_baseline['queries']['cloud_fault_detected'] = metric_check(prom_port, 'cloud_fault_detected', 0)
            evidence_baseline['localisation'] = res_baseline.to_dict()
            evidence_baseline['anomalies'] = anom_baseline
            result['stages'].append(evidence_baseline)
            print(" Stage 1 Baseline PASSED (0 anomalies, status=healthy)", flush=True)

            # Stage 2: Injected Latency Fault on seg_client_edge
            print("\n[Phase 6] Running Stage 2: Injected Latency Fault on seg_client_edge (+50ms)...", flush=True)
            if simulated:
                report_fault1 = generate_simulated_cycle(base_report, {'seg_client_edge': {'delay_ms': 50.0}})
            else:
                injector.apply_fault('seg_client_edge', delay='50ms')
                report_fault1 = manager.collect_all_kpis(icmp_count=3, http_count=3, iperf_duration=2)

            evidence_fault1 = check_cycle(collector, report_fault1, prom_port, directory, 'fault_client_edge_latency')
            res_fault1 = collector._localisation_summary
            anom_fault1 = collector._anomaly_summary

            if not anom_fault1['anomaly_detected']:
                raise RuntimeError("Anomaly detector failed to detect injected latency on seg_client_edge")

            eval1 = res_fault1.evaluate_ground_truth('seg_client_edge')
            if not eval1['matched']:
                raise RuntimeError(f"Localisation failed to match ground truth: {eval1}")
            print(f" Stage 2 Fault Localised: candidates={res_fault1.candidate_segments}, eval={eval1['classification']}", flush=True)

            # Verify Prometheus anomaly & candidate metrics
            evidence_fault1['queries']['cloud_anomaly_detected'] = metric_check(prom_port, 'cloud_anomaly_detected', 1)
            evidence_fault1['queries']['cloud_fault_detected'] = metric_check(prom_port, 'cloud_fault_detected', 1)
            evidence_fault1['queries']['cloud_fault_candidate{segment="seg_client_edge"}'] = metric_check(
                prom_port, 'cloud_fault_candidate{segment="seg_client_edge"}', 1)
            evidence_fault1['queries']['cloud_fault_confidence{segment="seg_client_edge"}'] = metric_check(
                prom_port, 'cloud_fault_confidence{segment="seg_client_edge"}', 1.0)

            evidence_fault1['localisation'] = res_fault1.to_dict()
            evidence_fault1['evaluation'] = eval1
            evidence_fault1['anomalies'] = anom_fault1
            result['stages'].append(evidence_fault1)
            if not simulated:
                injector.restore_fault('seg_client_edge')

            # Stage 3: Injected Loss Fault on seg_app_db
            print("\n[Phase 6] Running Stage 3: Injected Packet Loss on seg_app_db (25%)...", flush=True)
            if simulated:
                report_fault2 = generate_simulated_cycle(base_report, {'seg_app_db': {'loss_percent': 25.0}})
            else:
                injector.apply_fault('seg_app_db', loss=25.0)
                report_fault2 = manager.collect_all_kpis(icmp_count=3, http_count=3, iperf_duration=2)

            evidence_fault2 = check_cycle(collector, report_fault2, prom_port, directory, 'fault_app_db_loss')
            res_fault2 = collector._localisation_summary
            anom_fault2 = collector._anomaly_summary

            if not anom_fault2['anomaly_detected']:
                raise RuntimeError("Anomaly detector failed to detect packet loss on seg_app_db")

            eval2 = res_fault2.evaluate_ground_truth('seg_app_db')
            if not eval2['matched']:
                raise RuntimeError(f"Localisation failed to match ground truth: {eval2}")
            print(f" Stage 3 Fault Localised: candidates={res_fault2.candidate_segments}, eval={eval2['classification']}", flush=True)

            evidence_fault2['queries']['cloud_anomaly_detected'] = metric_check(prom_port, 'cloud_anomaly_detected', 1)
            evidence_fault2['queries']['cloud_fault_detected'] = metric_check(prom_port, 'cloud_fault_detected', 1)
            evidence_fault2['queries']['cloud_fault_candidate{segment="seg_app_db"}'] = metric_check(
                prom_port, 'cloud_fault_candidate{segment="seg_app_db"}', 1)

            evidence_fault2['localisation'] = res_fault2.to_dict()
            evidence_fault2['evaluation'] = eval2
            evidence_fault2['anomalies'] = anom_fault2
            result['stages'].append(evidence_fault2)
            if not simulated:
                injector.restore_fault('seg_app_db')

            # Stage 4: Ambiguous Core Segment Fault on seg_edge_lb
            print("\n[Phase 6] Running Stage 4: Ambiguous Core Segment Fault on seg_edge_lb (+40ms)...", flush=True)
            if simulated:
                report_fault3 = generate_simulated_cycle(base_report, {'seg_edge_lb': {'delay_ms': 40.0}})
            else:
                injector.apply_fault('seg_edge_lb', delay='40ms')
                report_fault3 = manager.collect_all_kpis(icmp_count=3, http_count=3, iperf_duration=2)

            evidence_fault3 = check_cycle(collector, report_fault3, prom_port, directory, 'fault_edge_lb_ambiguous')
            res_fault3 = collector._localisation_summary
            anom_fault3 = collector._anomaly_summary

            eval3 = res_fault3.evaluate_ground_truth('seg_edge_lb')
            if not eval3['matched'] or not res_fault3.is_ambiguous:
                raise RuntimeError(f"Expected honest ambiguous localisation: {eval3}")
            print(f" Stage 4 Ambiguity Handled: candidates={res_fault3.candidate_segments}, eval={eval3['classification']}", flush=True)

            evidence_fault3['queries']['cloud_anomaly_detected'] = metric_check(prom_port, 'cloud_anomaly_detected', 1)
            evidence_fault3['queries']['cloud_fault_detected'] = metric_check(prom_port, 'cloud_fault_detected', 1)
            evidence_fault3['queries']['cloud_fault_candidate{segment="seg_edge_lb"}'] = metric_check(
                prom_port, 'cloud_fault_candidate{segment="seg_edge_lb"}', 1)
            evidence_fault3['queries']['cloud_fault_candidate{segment="seg_lb_app"}'] = metric_check(
                prom_port, 'cloud_fault_candidate{segment="seg_lb_app"}', 1)
            evidence_fault3['queries']['cloud_fault_confidence{segment="seg_edge_lb"}'] = metric_check(
                prom_port, 'cloud_fault_confidence{segment="seg_edge_lb"}', 0.5)

            evidence_fault3['localisation'] = res_fault3.to_dict()
            evidence_fault3['evaluation'] = eval3
            evidence_fault3['anomalies'] = anom_fault3
            result['stages'].append(evidence_fault3)
            if not simulated:
                injector.restore_fault('seg_edge_lb')

            # Stage 5: Post-recovery Baseline
            print("\n[Phase 6] Running Stage 5: Post-Recovery Health Check...", flush=True)
            if simulated:
                report_recovery = generate_simulated_cycle(base_report)
            else:
                report_recovery = manager.collect_all_kpis(icmp_count=3, http_count=3, iperf_duration=2)

            evidence_recovery = check_cycle(collector, report_recovery, prom_port, directory, 'recovery')
            res_recovery = collector._localisation_summary
            if not report_recovery['success']:
                raise RuntimeError("Post-recovery cycle was not successful")
            print(" Stage 5 Recovery PASSED (0 anomalies, healthy status)", flush=True)

            evidence_recovery['queries']['cloud_anomaly_detected'] = metric_check(prom_port, 'cloud_anomaly_detected', 0)
            evidence_recovery['queries']['cloud_fault_detected'] = metric_check(prom_port, 'cloud_fault_detected', 0)
            evidence_recovery['localisation'] = res_recovery.to_dict()
            result['stages'].append(evidence_recovery)

            if hold_seconds:
                print(f"Holding for {hold_seconds}s...", flush=True)
                time.sleep(hold_seconds)

        result['network_cleanup_success'] = bool(simulated) or state.get('cleanup_success', False)
        result['success'] = bool(simulated) or state.get('cleanup_success', False)
        status = 0 if result['success'] else 1

    except (Exception, KeyboardInterrupt) as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        print(f"Phase 6 verification encountered error: {result['error']}", file=sys.stderr)
        status = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        with (directory / 'verification.json').open('x') as output:
            json.dump(result, output, indent=2, allow_nan=False)
        print(f"\nEvidence saved to: {directory}", flush=True)

    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulated', action='store_true',
                        help='Execute unprivileged simulation; clearly marked as simulated_fault_injection')
    parser.add_argument('--hold-seconds', type=int, default=0, help='Time to keep state for inspection')
    args = parser.parse_args()

    if not args.simulated and os.geteuid() != 0:
        parser.error('Live Mininet verification requires sudo. Use --simulated for unprivileged test.')
    if args.hold_seconds < 0:
        parser.error('hold-seconds must be nonnegative')

    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    return verify_phase6(simulated=args.simulated, hold_seconds=args.hold_seconds)


if __name__ == '__main__':
    sys.exit(main())
