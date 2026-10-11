#!/usr/bin/env python3
"""Collect real Mininet measurements continuously and expose them to Prometheus."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.monitoring.exporter import MeasurementCollector, metrics_server
from src.monitoring.prometheus import prometheus_server
from src.monitoring.grafana import grafana_server, DASHBOARD_UID
from src.topology.lifecycle import experiment_network
from src.traffic.manager import TrafficManager
from scripts.run_traffic_test import save_results


def run(cycles=0, interval=5, exporter_port=9200, prometheus_port=9091, output_dir=None,
        grafana=False, grafana_port=3000):
    directory = Path(output_dir or ROOT / 'results' / f'monitoring_{time.time_ns()}')
    directory.mkdir(parents=True, exist_ok=False)
    collector = MeasurementCollector()
    state = {}
    completed = 0
    error = None
    status = 0
    try:
        with ExitStack() as stack:
            stack.enter_context(metrics_server(collector, exporter_port))
            stack.enter_context(prometheus_server(directory / 'prometheus', exporter_port, prometheus_port))
            if grafana:
                stack.enter_context(grafana_server(directory / 'grafana', prometheus_port, grafana_port))
            net = stack.enter_context(experiment_network(state))
            manager = TrafficManager(net)
            stack.callback(manager.teardown_services)
            manager.setup_services()
            print(f'Metrics: http://127.0.0.1:{exporter_port}/metrics', flush=True)
            print(f'Prometheus: http://127.0.0.1:{prometheus_port}', flush=True)
            if grafana:
                print(f'Dashboard: http://127.0.0.1:{grafana_port}/d/{DASHBOARD_UID}', flush=True)
            print(f'Measurements: {directory}', flush=True)
            while cycles == 0 or completed < cycles:
                start = time.monotonic()
                try:
                    measurements = manager.collect_all_kpis(icmp_count=3, http_count=3, iperf_duration=2)
                    collector.update(measurements, time.monotonic() - start)
                    path = save_results(measurements, directory)
                    completed += 1
                    print(f'Cycle {completed}: baseline_ok={measurements["success"]}; {path.name}', flush=True)
                except Exception as exc:
                    collector.collection_failed(time.monotonic() - start)
                    raise RuntimeError(f'Measurement collection failed: {exc}') from exc
                if cycles == 0 or completed < cycles:
                    time.sleep(interval)
    except KeyboardInterrupt:
        status = 130
        error = 'Stopped by user'
    except Exception as exc:
        status = 1
        error = f'{type(exc).__name__}: {exc}'
        print(error, file=sys.stderr)
    finally:
        with (directory / 'session.json').open('x') as stream:
            json.dump({'cycles': completed, 'exit_code': status, 'error': error,
                       'network_cleanup_success': state.get('cleanup_success', False)}, stream, indent=2)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycles', type=int, default=0, help='0 runs until Ctrl+C')
    parser.add_argument('--interval', type=float, default=5, help='Idle seconds between complete cycles')
    parser.add_argument('--exporter-port', type=int, default=9200)
    parser.add_argument('--prometheus-port', type=int, default=9091)
    parser.add_argument('--grafana', action='store_true', help='Start the provisioned dashboard')
    parser.add_argument('--grafana-port', type=int, default=3000)
    args = parser.parse_args()
    if args.cycles < 0 or args.interval < 0:
        parser.error('cycles and interval must be nonnegative')
    if os.geteuid() != 0:
        parser.error('Mininet needs sudo')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    return run(**vars(args))


if __name__ == '__main__':
    sys.exit(main())
