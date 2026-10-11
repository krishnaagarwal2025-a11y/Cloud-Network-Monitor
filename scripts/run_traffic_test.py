#!/usr/bin/env python3
"""
Traffic Generation & KPI Measurement Runner
-------------------------------------------
Starts the Mininet cloud topology, launches application services (HTTP, iperf3),
runs comprehensive measurements (ICMP, HTTP, throughput), prints formatted KPI
summaries, and saves structured JSON metrics for telemetry ingestion.

Usage:
    sudo python3 scripts/run_traffic_test.py
"""

import json
import os
import sys
import time
import argparse
import signal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.topology.lifecycle import experiment_network
from src.traffic.manager import TrafficManager


def format_table(headers, rows):
    """Simple formatted table printer."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))
            
    header_line = " | ".join(f"{h:<{w}}" for h, w in zip(headers, col_widths))
    sep_line = "-+-".join("-" * w for w in col_widths)
    output = [header_line, sep_line]
    for row in rows:
        output.append(" | ".join(f"{str(v):<{w}}" for v, w in zip(row, col_widths)))
    return "\n".join(output)


def print_measurements(kpis):
    """Render the exact records saved to JSON, including failure diagnostics."""
    for group, fields in (
        ('icmp', ['source', 'target', 'packets_sent', 'packets_received',
                  'packet_loss_percent', 'rtt_min_ms', 'rtt_avg_ms', 'rtt_max_ms',
                  'rtt_mdev_ms', 'success', 'error_message']),
        ('http', ['source', 'target', 'http_status', 'connect_time_ms',
                  'response_time_ms', 'success', 'error_message']),
        ('throughput', ['source', 'target', 'duration_sec', 'throughput_mbps',
                        'retransmits', 'mean_rtt_ms', 'success', 'error_message']),
    ):
        print('\n' + group.upper())
        print(format_table(fields, [
            ['N/A' if r.get(f) is None else r.get(f) for f in fields]
            for r in kpis[group]]))
    print('\nSummary (null means unavailable):')
    print(json.dumps(kpis['summary'], indent=2, allow_nan=False))


def save_results(report, output_dir):
    """Create a unique report without replacing earlier measurements."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f'traffic_metrics_{time.time_ns()}_{uuid4().hex[:8]}.json'
    payload = json.dumps(report, indent=2, allow_nan=False)
    with path.open('x') as stream:
        stream.write(payload + '\n')
    return path


def run_traffic_experiment(output_dir=None):
    output_dir = output_dir or Path(__file__).resolve().parents[1] / 'results'
    report = {'schema_version': 1, 'timestamp': time.time(), 'success': False,
              'stage': 'startup', 'errors': [], 'cleanup_success': False}
    exit_code = 1
    cleanup_errors = []
    entered = False
    services = []
    network_state = {}
    try:
        with experiment_network(network_state) as net:
            entered = True
            print(f'Topology started: {len(net.hosts)} hosts, {len(net.switches)} switches')
            manager = TrafficManager(net)
            try:
                manager.setup_services()
                services = manager.services
                print('HTTP and both iperf3 servers verified listening.')
                report['stage'] = 'measurements'
                kpis = manager.collect_all_kpis(icmp_count=3, http_count=5, iperf_duration=3)
                report.update(kpis)
                print_measurements(kpis)
                if not kpis['success']:
                    report['errors'].append('One or more baseline probes failed; inspect measurement records')
                exit_code = 0 if kpis['success'] else 1
                report['stage'] = 'completed'
            finally:
                try:
                    manager.teardown_services()
                except Exception as exc:
                    cleanup_errors.append(str(exc))
                    raise
    except KeyboardInterrupt:
        report['errors'].append('Experiment interrupted')
        exit_code = 130
    except Exception as exc:
        report['errors'].append(f'{type(exc).__name__}: {exc}')
        exit_code = 1
    finally:
        # Report verified teardown separately from probe success.
        report['cleanup_success'] = (network_state.get('cleanup_success', False)
                                     and not cleanup_errors
                                     and all(s.process.poll() is not None for s in services))
        report['errors'].extend(cleanup_errors)
        report['success'] = exit_code == 0 and report['cleanup_success']
        report['network_started'] = entered
        report['service_pids'] = [s.pid for s in services]
        if not report['success'] and exit_code == 0:
            exit_code = 1
        path = save_results(report, output_dir)
        print(f'\nSaved experiment report: {path}')
        for error in report['errors']:
            print(f'ERROR: {error}', file=sys.stderr)
        print('Experiment PASS' if report['success'] else 'Experiment FAILED')
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'results')
    args = parser.parse_args()
    if os.geteuid() != 0:
        print('Live Mininet experiments require sudo.', file=sys.stderr)
        return 1
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    return run_traffic_experiment(args.output_dir)


if __name__ == '__main__':
    sys.exit(main())
