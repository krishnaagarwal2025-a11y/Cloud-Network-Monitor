#!/usr/bin/env python3
"""Verify Prometheus ingestion of actual measurements and application failure status."""

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import signal
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.monitoring.exporter import MeasurementCollector, metrics_server
from src.monitoring.prometheus import prometheus_server, wait_query, get_json
from src.topology.lifecycle import experiment_network
from src.traffic.manager import TrafficManager
from scripts.run_traffic_test import save_results


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def metric_check(port, expression, expected):
    return wait_query(port, expression, lambda rows: len(rows) == 1 and
                      math.isclose(float(rows[0]['value'][1]), expected, rel_tol=0, abs_tol=1e-6))


def check_cycle(collector, report, prom_port, directory, stage, duration=0):
    collector.update(report, duration)
    path = save_results(report, directory)
    web_ping = next(r for r in report['icmp'] if r['source'] == 'client' and r['target'] == 'web_srv')
    web_iperf = next(r for r in report['throughput'] if r['source'] == 'client' and r['target'] == 'web_srv')
    suffix = '{source="client",target="web_srv"}'
    evidence = {'stage': stage, 'report': str(path), 'queries': {}}
    checks = {
        'up{job="cloud-network-monitor"}': 1,
        'cloud_collection_success': 1,
        'cloud_probe_timestamp_seconds{probe="http",source="client",target="web_srv"}':
            max(r['timestamp'] for r in report['http']),
        'cloud_probe_success{probe="http",source="client",target="web_srv"}':
            int(all(r['success'] for r in report['http'])),
        'cloud_ping_latency_seconds' + suffix: web_ping['rtt_avg_ms'] / 1000,
        'cloud_packet_loss_ratio' + suffix: web_ping['packet_loss_percent'] / 100,
        'cloud_throughput_bits_per_second' + suffix: web_iperf['throughput_mbps'] * 1e6,
    }
    for expression, expected in checks.items():
        evidence['queries'][expression] = metric_check(prom_port, expression, expected)
    if all(r['success'] for r in report['http']):
        expression = 'cloud_http_response_seconds' + suffix
        expected = sum(r['response_time_ms'] for r in report['http']) / len(report['http']) / 1000
        evidence['queries'][expression] = metric_check(prom_port, expression, expected)
    else:
        evidence['queries']['http_latency_absent'] = wait_query(
            prom_port, 'cloud_http_response_seconds' + suffix, lambda rows: rows == [])
    return evidence


def verify(recorded_report=None):
    mode = 'recorded_measurement_ingestion' if recorded_report else 'live_mininet'
    directory = ROOT / 'results' / f'phase4_verification_{time.time_ns()}'
    directory.mkdir(parents=True, exist_ok=False)
    state = {}
    report = {'success': False, 'mode': mode, 'stages': [], 'network_cleanup_success': False}
    status = 1
    try:
        collector = MeasurementCollector()
        with ExitStack() as stack:
            exporter_port = stack.enter_context(metrics_server(collector, 0))
            prom_port = free_port()
            stack.enter_context(prometheus_server(directory / 'prometheus', exporter_port, prom_port))
            if recorded_report:
                source = Path(recorded_report).resolve()
                measurements = json.loads(source.read_text())
                report['source_report'] = str(source)
                report['stages'].append(check_cycle(collector, measurements, prom_port, directory, 'recorded'))
            else:
                net = stack.enter_context(experiment_network(state))
                manager = TrafficManager(net)
                stack.callback(manager.teardown_services)
                manager.setup_services()
                previous_timestamp = 0
                for stage in ('baseline', 'application_down', 'recovery'):
                    if stage == 'application_down':
                        manager.stop_http_service()
                    elif stage == 'recovery':
                        manager.start_http_service()
                    start = time.monotonic()
                    measurements = manager.collect_all_kpis(icmp_count=3, http_count=3, iperf_duration=2)
                    if measurements['timestamp'] <= previous_timestamp:
                        raise RuntimeError('Measurement timestamps did not advance')
                    previous_timestamp = measurements['timestamp']
                    if measurements['success'] != (stage != 'application_down'):
                        raise RuntimeError(f'Unexpected measured health during {stage}')
                    if not all(r['success'] for r in measurements['icmp'] + measurements['throughput']):
                        raise RuntimeError(f'Network probes failed during application-only test: {stage}')
                    if any(r['success'] != (stage != 'application_down') for r in measurements['http']):
                        raise RuntimeError(f'Unexpected HTTP responses during {stage}')
                    report['stages'].append(check_cycle(collector, measurements, prom_port,
                                                        directory, stage, time.monotonic() - start))
                    print(f'Verified {stage} in Prometheus', flush=True)
            report['targets'] = get_json(f'http://127.0.0.1:{prom_port}/api/v1/targets')
        report['network_cleanup_success'] = state.get('cleanup_success', False)
        report['success'] = bool(recorded_report) or report['network_cleanup_success']
        status = 0 if report['success'] else 1
    except (Exception, KeyboardInterrupt) as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        print(report['error'], file=sys.stderr)
        status = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        with (directory / 'verification.json').open('x') as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
        print(f'Evidence ({mode}): {directory}', flush=True)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recorded-report', type=Path,
                        help='Unprivileged ingestion check only; does not verify live collection')
    args = parser.parse_args()
    if args.recorded_report is None and os.geteuid() != 0:
        parser.error('Live verification requires sudo')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    return verify(args.recorded_report)


if __name__ == '__main__':
    sys.exit(main())
