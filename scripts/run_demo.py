#!/usr/bin/env python3
"""Reproducible normal/fault/recovery experiment, with optional synthetic mode."""

import argparse
from contextlib import ExitStack
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import signal
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.configuration import DEFAULT_CONFIG
from src.analysis.evaluation import evaluate
from src.analysis.localiser import PATH_SEGMENT_MAP
from src.monitoring.exporter import MeasurementCollector, metrics_server
from src.monitoring.grafana import grafana_server, api, query_panel, DASHBOARD_UID
from src.monitoring.prometheus import prometheus_server
from src.topology.cloud_topology import SEGMENTS
from src.topology.lifecycle import experiment_network
from src.traffic.fault_injection import FaultInjector
from src.traffic.manager import TrafficManager
from scripts.run_traffic_test import save_results
from scripts.verify_phase4 import free_port, metric_check
from scripts.verify_phase5 import numbers


def read_config(path):
    config = json.loads(Path(path).read_text())
    keys = {'baseline_cycles', 'fault_cycles', 'recovery_cycles', 'icmp_count',
            'http_count', 'iperf_duration', 'scenarios'}
    if set(config) != keys:
        raise ValueError('Experiment configuration has missing/unknown fields')
    for name in keys - {'scenarios'}:
        if type(config[name]) is not int or config[name] < 1:
            raise ValueError(f'{name} must be a positive integer')
    if config['baseline_cycles'] < 6:
        raise ValueError('At least six baseline cycles are required for statistical warm-up')
    names = set()
    for scenario in config['scenarios']:
        if set(scenario) != {'name', 'condition', 'segment', 'parameters'}:
            raise ValueError('Invalid scenario fields')
        if not scenario['name'] or scenario['name'] in names:
            raise ValueError('Scenario names must be nonempty and unique')
        names.add(scenario['name'])
        condition = scenario['condition']
        if condition not in ('latency', 'loss', 'bandwidth', 'application_down'):
            raise ValueError('Unsupported experiment condition')
        if condition == 'application_down':
            if scenario['segment'] is not None or scenario['parameters']:
                raise ValueError('Application outage has no link parameters')
        else:
            if scenario['segment'] not in SEGMENTS:
                raise ValueError('Scenario refers to unknown segment')
            expected = {'latency': 'delay', 'loss': 'loss', 'bandwidth': 'bw'}[condition]
            if set(scenario['parameters']) != {expected}:
                raise ValueError('Condition does not match impairment parameters')
            # Validate through the existing injector without touching any interfaces.
            FaultInjector().apply_fault(scenario['segment'], **scenario['parameters'])
    if not names:
        raise ValueError('At least one scenario is required')
    return config


def synthetic_cycle(base, scenario=None):
    """Explicit deterministic simulation; never presented as real network traffic."""
    report = deepcopy(base)
    now = time.time()
    report['timestamp'] = now
    report['measurement_mode'] = 'synthetic_simulation'
    for group in ('icmp', 'http', 'throughput'):
        for row in report[group]:
            row['timestamp'] = now
            row['raw_output'] = 'SYNTHETIC: modeled observation, no live probe was executed'
    if scenario is not None:
        condition, segment, parameters = (scenario[k] for k in ('condition', 'segment', 'parameters'))
        for group in ('icmp', 'http', 'throughput'):
            for row in report[group]:
                if condition == 'application_down':
                    if group == 'http':
                        row.update(success=False, http_status=0, response_time_ms=None,
                                   connect_time_ms=None, exit_code=7, error_message='Synthetic application outage')
                    continue
                if segment not in PATH_SEGMENT_MAP.get((row['source'], row['target']), []):
                    continue
                if condition == 'latency':
                    def milliseconds(value):
                        units = ('us', 'ms', 's')
                        unit = next(u for u in units if value.endswith(u))
                        return float(value[:-len(unit)]) * {'us': .001, 'ms': 1, 's': 1000}[unit]
                    delta = 2 * (milliseconds(parameters['delay']) - milliseconds(SEGMENTS[segment]['delay']))
                    for field in (('rtt_min_ms', 'rtt_avg_ms', 'rtt_max_ms') if group == 'icmp'
                                  else ('response_time_ms', 'connect_time_ms') if group == 'http' else ()):
                        if row.get(field) is not None:
                            row[field] += max(0, delta)
                elif condition == 'loss':
                    loss = parameters['loss']
                    if group == 'icmp':
                        row['packet_loss_percent'] = loss
                        if loss == 100:
                            row.update(success=False, packets_received=0, exit_code=1)
                            for field in ('rtt_min_ms', 'rtt_avg_ms', 'rtt_max_ms', 'rtt_mdev_ms'):
                                row[field] = None
                    elif loss == 100 and group == 'http':
                        row.update(success=False, http_status=0, response_time_ms=None, connect_time_ms=None,
                                   exit_code=28, error_message='Synthetic network timeout')
                    elif loss == 100:
                        row.update(success=False, throughput_mbps=None, duration_sec=None, mean_rtt_ms=None,
                                   retransmits=None, exit_code=1, error_message='Synthetic network timeout')
                elif condition == 'bandwidth' and group == 'throughput':
                    row['throughput_mbps'] = min(row['throughput_mbps'], parameters['bw'] * .9)
    primary = next(p for p in report['icmp'] if p['source'] == 'client' and p['target'] == 'web_srv')
    http = report['http']
    throughput = next(p for p in report['throughput'] if p['target'] == 'web_srv')
    report['summary'] = {
        'ping_latency_ms': primary['rtt_avg_ms'], 'packet_loss_percent': primary['packet_loss_percent'],
        'http_response_time_ms': (round(statistics.mean(p['response_time_ms'] for p in http), 2)
                                  if all(p['success'] for p in http) else None),
        'http_success': int(all(p['success'] for p in http)), 'throughput_mbps': throughput['throughput_mbps'],
    }
    report['success'] = (all(p['success'] and p['packet_loss_percent'] == 0 for p in report['icmp'])
                         and all(p['success'] for p in http + report['throughput']))
    return report


def verify_dashboard(port):
    evidence = {}
    end = int(time.time() * 1000)
    for uid in (DASHBOARD_UID, 'cloud-anomaly-detection'):
        dashboard = api(port, f'/api/dashboards/uid/{uid}')
        if not dashboard['meta'].get('provisioned'):
            raise RuntimeError(f'Dashboard is not provisioned: {uid}')
        panels = {}
        for panel in dashboard['dashboard']['panels']:
            if panel.get('targets'):
                result = query_panel(port, panel, end - 900000, end)
                panels[panel['title']] = {'numeric_samples': len(numbers(result)), 'response': result}
        if not any(p['numeric_samples'] for p in panels.values()):
            raise RuntimeError(f'No measurements in dashboard {uid}')
        evidence[uid] = panels
    return evidence


def run(simulated=False, config_path=None, analysis_config=None, grafana=True,
        hold_seconds=0, directory=None):
    config = read_config(config_path or ROOT / 'config/experiment.json')
    directory = Path(directory or ROOT / 'results' / f'end_to_end_{time.time_ns()}')
    directory.mkdir(parents=True, exist_ok=False)
    result = {'schema_version': 1, 'success': False,
              'mode': 'synthetic_simulation' if simulated else 'live_mininet',
              'settings': config, 'events': [], 'cycles': [], 'checks': {}, 'errors': []}
    state = {}
    status = 1
    collector = MeasurementCollector(analysis_config)
    result['analysis_settings'] = collector.analysis_settings
    injector = None
    service_handles = []

    def checkpoint():
        # This file belongs to this unique run and intentionally tracks partial progress.
        temporary = directory / 'experiment.partial'
        temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
        temporary.replace(directory / 'experiment.json')

    def event(scenario, state_name, **details):
        item = {'scenario': scenario['name'], 'condition': scenario['condition'],
                'segment': scenario['segment'], 'timestamp': time.time(), 'state': state_name,
                'mode': result['mode'], **details}
        result['events'].append(item)
        checkpoint()
        return item

    try:
        with ExitStack() as stack:
            exporter_port = stack.enter_context(metrics_server(collector, 0))
            prometheus_port = free_port()
            stack.enter_context(prometheus_server(directory / 'prometheus', exporter_port, prometheus_port))
            grafana_port = None
            if grafana:
                grafana_port = free_port()
                stack.enter_context(grafana_server(directory / 'grafana', prometheus_port, grafana_port))
                result['dashboard_url'] = f'http://127.0.0.1:{grafana_port}/d/{DASHBOARD_UID}'
                print(f'Dashboard: {result["dashboard_url"]}', flush=True)
            if simulated:
                base = json.loads((ROOT / 'tests/fixtures/simulated_baseline.json').read_text())
                manager = None
                injector = FaultInjector()
            else:
                net = stack.enter_context(experiment_network(state))
                manager = TrafficManager(net)
                stack.callback(manager.teardown_services)
                manager.setup_services()
                service_handles.extend(manager.services)
                injector = FaultInjector(net)
                stack.callback(injector.restore_all)

            def collect(condition, scenario=None, warmup=False):
                collector.experiment_state(condition, scenario['segment'] if scenario else None,
                                           applied=scenario is not None and condition not in ('baseline', 'recovery'))
                start = time.monotonic()
                report = (synthetic_cycle(base, scenario if condition not in ('baseline', 'recovery') else None)
                          if simulated else manager.collect_all_kpis(
                              icmp_count=config['icmp_count'], http_count=config['http_count'],
                              iperf_duration=config['iperf_duration']))
                report['measurement_mode'] = result['mode']
                collector.update(report, time.monotonic() - start)
                saved = save_results(report, directory)
                findings = collector.analysis_snapshot()
                record = {'sequence': len(result['cycles']), 'timestamp': report['timestamp'],
                          'condition': condition, 'scenario': scenario['name'] if scenario else 'baseline',
                          'segment': scenario['segment'] if scenario else None, 'warmup': warmup,
                          'report': saved.name, 'summary': report['summary'], 'analysis': findings}
                result['cycles'].append(record)
                expected = int(findings['anomalies']['anomaly_detected'])
                record['prometheus'] = {
                    'up': metric_check(prometheus_port, 'up{job="cloud-network-monitor"}', 1),
                    'anomaly': metric_check(prometheus_port, 'cloud_anomaly_detected', expected),
                    'measurement_time': metric_check(prometheus_port, 'cloud_collection_timestamp_seconds',
                                                     collector._attempt_time),
                }
                checkpoint()
                print(f'Cycle {record["sequence"] + 1}: {condition}, '
                      f'anomaly={bool(expected)}, localization={findings["localisation"]["status"]}', flush=True)
                return report, findings

            baseline_reports = []
            for i in range(config['baseline_cycles']):
                report, _ = collect('baseline', warmup=i < config['baseline_cycles'] - 1)
                if not report['success']:
                    raise RuntimeError('Baseline probes failed; refusing to inject a fault')
                baseline_reports.append(report)
            if grafana:
                result['checks']['baseline_dashboards'] = verify_dashboard(grafana_port)
            expected_candidates = {
                'seg_client_edge': ['seg_client_edge'], 'seg_app_db': ['seg_app_db'],
                'seg_edge_lb': ['seg_edge_lb', 'seg_lb_app'], 'seg_lb_app': ['seg_edge_lb', 'seg_lb_app'],
            }
            for scenario in config['scenarios']:
                event(scenario, 'requested', parameters=scenario['parameters'])
                before = None
                if scenario['segment']:
                    before = injector.verify_segment(scenario['segment'])
                event(scenario, 'baseline_recorded', kernel_state=before)
                try:
                    if scenario['condition'] == 'application_down':
                        if manager:
                            manager.stop_http_service()
                        applied = None
                    else:
                        applied = injector.apply_fault(scenario['segment'], **scenario['parameters'])
                        observed = injector.verify_segment(scenario['segment'], applied.parameters)
                    event(scenario, 'applied', kernel_state=observed if applied else None)
                    fault_findings = []
                    for _ in range(config['fault_cycles']):
                        report, findings = collect(scenario['condition'], scenario)
                        fault_findings.append(findings)
                    if not any(f['anomalies']['by_detector']['threshold'] for f in fault_findings):
                        raise RuntimeError(f'Threshold detector missed controlled impairment: {scenario["name"]}')
                    if scenario['condition'] in ('latency', 'loss'):
                        if not any(f['localisation']['candidate_segments'] == expected_candidates[scenario['segment']]
                                   for f in fault_findings):
                            raise RuntimeError(f'Expected network inference not observed: {scenario["name"]}')
                    if scenario['condition'] == 'application_down' and any(
                            f['localisation']['candidate_segments'] for f in fault_findings):
                        raise RuntimeError('Application outage was incorrectly attributed to a segment')
                    if grafana:
                        result['checks'][scenario['name'] + '_dashboards'] = verify_dashboard(grafana_port)
                finally:
                    if scenario['condition'] == 'application_down':
                        if manager:
                            manager.start_http_service()
                            service_handles.extend(manager.services)
                        restored = None
                    else:
                        injector.restore_fault(scenario['segment'])
                        restored = injector.verify_segment(scenario['segment'])
                        if before['interfaces'] and [p['parameters'] for p in before['interfaces']] != [
                                p['parameters'] for p in restored['interfaces']]:
                            raise RuntimeError('Restored kernel parameters differ from pre-fault configuration')
                    event(scenario, 'restored', kernel_state=restored)
                recovered = []
                for _ in range(config['recovery_cycles']):
                    report, findings = collect('recovery', scenario)
                    recovered.append((report, findings))
                last, findings = recovered[-1]
                baseline_rtt = statistics.median(r['summary']['ping_latency_ms'] for r in baseline_reports)
                baseline_tp = statistics.median(r['summary']['throughput_mbps'] for r in baseline_reports)
                if (not last['success'] or findings['localisation']['status'] != 'healthy'
                        or findings['anomalies']['by_detector']['threshold'] != 0
                        or last['summary']['ping_latency_ms'] > baseline_rtt * 1.5 + 5
                        or last['summary']['throughput_mbps'] < baseline_tp * .7):
                    raise RuntimeError(f'Measured recovery failed: {scenario["name"]}')
                event(scenario, 'recovery_verified', summary=last['summary'])
            result['evaluation'] = evaluate(result['cycles'], result['events'])
            if grafana:
                result['checks']['final_dashboards'] = verify_dashboard(grafana_port)
            if hold_seconds:
                print(f'Available for browser review for {hold_seconds}s', flush=True)
                # Signal interruption still exits through all cleanup contexts.
                deadline = time.monotonic() + hold_seconds
                while time.monotonic() < deadline:
                    time.sleep(min(1, deadline - time.monotonic()))
        result['network_cleanup_success'] = None if simulated else state.get('cleanup_success', False)
        result['service_cleanup_success'] = all(s.process.poll() is not None for s in service_handles)
        result['success'] = (not injector.active_faults and result['service_cleanup_success']
                             and (simulated or result['network_cleanup_success']))
        status = 0 if result['success'] else 1
    except (Exception, KeyboardInterrupt) as exc:
        result['errors'].append(f'{type(exc).__name__}: {exc}')
        status = 130 if isinstance(exc, KeyboardInterrupt) else 1
        print(result['errors'][-1], file=sys.stderr, flush=True)
    finally:
        result['evaluation'] = evaluate(result['cycles'], result['events'])
        result['network_cleanup_success'] = None if simulated else state.get('cleanup_success', False)
        result['active_faults_after_cleanup'] = list(injector.active_faults) if injector else []
        result['exit_code'] = status
        checkpoint()
        from src.analysis.reporting import write_report
        write_report(directory)
        print(f'Evidence ({result["mode"]}): {directory}', flush=True)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--simulated', action='store_true', help='Synthetic observations, real monitoring services')
    parser.add_argument('--no-grafana', action='store_true')
    parser.add_argument('--config', type=Path, default=ROOT / 'config/experiment.json')
    parser.add_argument('--analysis-config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--hold-seconds', type=int, default=0)
    args = parser.parse_args()
    if not args.simulated and os.geteuid() != 0:
        parser.error('Live demonstration requires sudo; --simulated never executes Mininet or tc')
    if args.hold_seconds < 0:
        parser.error('hold-seconds must be nonnegative')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    for name in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(name, interrupted)
    return run(simulated=args.simulated, config_path=args.config,
               analysis_config=args.analysis_config, grafana=not args.no_grafana,
               hold_seconds=args.hold_seconds)


if __name__ == '__main__':
    sys.exit(main())
