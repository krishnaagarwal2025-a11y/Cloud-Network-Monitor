#!/usr/bin/env python3
"""Verify provisioned Grafana panels against real measurement changes."""

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
from src.monitoring.grafana import grafana_server, api, query_panel, DASHBOARD_UID
from src.topology.lifecycle import experiment_network
from src.traffic.manager import TrafficManager
from scripts.verify_phase4 import check_cycle, free_port


def numbers(result):
    values = []
    for response in result.get('results', {}).values():
        if response.get('error') or response.get('status', 200) >= 400:
            raise RuntimeError(f'Grafana panel query failed: {response.get("error")}')
        for frame in response.get('frames', []):
            for field, column in zip(frame['schema']['fields'], frame['data']['values']):
                if field['type'] == 'number':
                    values.extend(v for v in column if v is not None)
    return values


def check_dashboard(port, expected_healthy):
    dashboard = api(port, f'/api/dashboards/uid/{DASHBOARD_UID}')
    if not dashboard['meta'].get('provisioned'):
        raise RuntimeError('Dashboard was not provisioned from project files')
    evidence = {}
    end = int(time.time() * 1000)
    for panel in dashboard['dashboard']['panels']:
        if not panel.get('targets'):
            continue
        result = query_panel(port, panel, end - 300000, end)
        observed = numbers(result)
        if not observed and panel['title'] not in ('Web response and connection time', 'TCP retransmissions per test'):
            raise RuntimeError(f'Grafana panel has no measured data: {panel["title"]}')
        if panel['title'] in ('Web availability', 'Baseline checks'):
            if observed != [int(expected_healthy)]:
                raise RuntimeError(f'Unexpected {panel["title"]} values: {observed}')
        if panel['title'] == 'Exporter reachability' and observed != [1]:
            raise RuntimeError('Grafana did not see a reachable exporter')
        evidence[panel['title']] = result
    return evidence


def verify(recorded_directory=None, hold_seconds=0):
    directory = ROOT / 'results' / f'phase5_verification_{time.time_ns()}'
    directory.mkdir(parents=True, exist_ok=False)
    mode = 'recorded_measurement_playback' if recorded_directory else 'live_mininet'
    result = {'success': False, 'mode': mode, 'stages': []}
    state = {}
    status = 1
    try:
        collector = MeasurementCollector()
        with ExitStack() as stack:
            exporter_port = stack.enter_context(metrics_server(collector, 0))
            prom_port = free_port()
            stack.enter_context(prometheus_server(directory / 'prometheus', exporter_port, prom_port))
            grafana_port = free_port()
            stack.enter_context(grafana_server(directory / 'grafana', prom_port, grafana_port))
            result['dashboard_url'] = f'http://127.0.0.1:{grafana_port}/d/{DASHBOARD_UID}'
            print(f'Dashboard: {result["dashboard_url"]}', flush=True)
            if recorded_directory:
                verification = json.loads((Path(recorded_directory) / 'verification.json').read_text())
                if not verification['success'] or verification['mode'] != 'live_mininet':
                    raise RuntimeError('Playback requires a successful live Phase 4 record')
                records = {s['stage']: json.loads(Path(s['report']).read_text()) for s in verification['stages']}
            else:
                net = stack.enter_context(experiment_network(state))
                manager = TrafficManager(net)
                stack.callback(manager.teardown_services)
                manager.setup_services()
            for stage in ('baseline', 'application_down', 'recovery'):
                if recorded_directory:
                    report = records[stage]
                else:
                    if stage == 'application_down':
                        manager.stop_http_service()
                    elif stage == 'recovery':
                        manager.start_http_service()
                    report = manager.collect_all_kpis(icmp_count=3, http_count=3, iperf_duration=2)
                healthy = stage != 'application_down'
                if report['success'] != healthy:
                    raise RuntimeError(f'Unexpected measured health: {stage}')
                evidence = check_cycle(collector, report, prom_port, directory, stage)
                # Leave multiple real scrape points for the time-series panels.
                time.sleep(3)
                evidence['panels'] = check_dashboard(grafana_port, healthy)
                result['stages'].append(evidence)
                print(f'Grafana panel queries verified: {stage}', flush=True)
            if hold_seconds:
                print(f'Keeping dashboard available for visual review for {hold_seconds}s.', flush=True)
                time.sleep(hold_seconds)
        result['network_cleanup_success'] = state.get('cleanup_success')
        result['success'] = bool(recorded_directory) or state.get('cleanup_success', False)
        status = 0 if result['success'] else 1
    except (Exception, KeyboardInterrupt) as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        print(result['error'], file=sys.stderr)
        status = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        with (directory / 'verification.json').open('x') as output:
            json.dump(result, output, indent=2, allow_nan=False)
        print(f'Evidence ({mode}): {directory}', flush=True)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recorded-directory', type=Path,
                        help='Replay genuine Phase 4 records; does not claim new traffic measurements')
    parser.add_argument('--hold-seconds', type=int, default=0, help='Time for browser inspection after checks')
    args = parser.parse_args()
    if args.recorded_directory is None and os.geteuid() != 0:
        parser.error('Live Mininet verification requires sudo')
    if args.hold_seconds < 0:
        parser.error('hold-seconds must be nonnegative')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    return verify(**vars(args))


if __name__ == '__main__':
    sys.exit(main())
