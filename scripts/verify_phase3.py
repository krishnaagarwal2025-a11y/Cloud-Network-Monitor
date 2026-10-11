#!/usr/bin/env python3
"""Run privileged Phase 3 acceptance checks and retain genuine evidence."""

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def verify_report(report):
    """Cross-check measured records and summaries without trusting a PASS banner."""
    assert report['success'], report.get('errors')
    assert report['cleanup_success']
    assert len(report['icmp']) == 5
    assert len(report['http']) == 5
    assert len(report['throughput']) == 2
    for measurement in report['icmp']:
        assert measurement['success'] and measurement['exit_code'] == 0
        assert measurement['packets_sent'] == measurement['packets_received'] == 3
        assert measurement['packet_loss_percent'] == 0
        assert measurement['rtt_avg_ms'] > 0
    for measurement in report['http']:
        assert measurement['success'] and measurement['exit_code'] == 0
        assert measurement['http_status'] == 200 and measurement['response_time_ms'] > 0
    for measurement in report['throughput']:
        assert measurement['success'] and measurement['exit_code'] == 0
        assert math.isfinite(measurement['throughput_mbps'])
        assert measurement['throughput_mbps'] > 0 and measurement['duration_sec'] > 0
    primary_ping = next(r for r in report['icmp']
                        if r['source'] == 'client' and r['target'] == 'web_srv')
    primary_throughput = next(r for r in report['throughput'] if r['target'] == 'web_srv')
    assert report['summary'] == {
        'ping_latency_ms': primary_ping['rtt_avg_ms'],
        'packet_loss_percent': primary_ping['packet_loss_percent'],
        'http_response_time_ms': round(sum(r['response_time_ms'] for r in report['http']) / 5, 2),
        'http_success': 1,
        'throughput_mbps': primary_throughput['throughput_mbps'],
    }
    for pid in report['service_pids']:
        assert not Path(f'/proc/{pid}').exists(), f'Experiment child {pid} still exists'


def main():
    if os.geteuid() != 0:
        print('Run with sudo: creates temporary Mininet network resources and traffic.', file=sys.stderr)
        return 1
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    evidence = ROOT / 'results' / f'phase3_verification_{time.time_ns()}'
    evidence.mkdir(parents=True, exist_ok=False)
    commands = [
        ('tests', [sys.executable, '-m', 'unittest', 'discover', '-v', '-s', 'tests']),
        ('experiment_1', [sys.executable, 'scripts/run_traffic_test.py', '--output-dir', str(evidence)]),
        ('experiment_2', [sys.executable, 'scripts/run_traffic_test.py', '--output-dir', str(evidence)]),
    ]
    print(f'Logs and reports: {evidence}', flush=True)
    outcomes = []
    child = None
    try:
        for name, argv in commands:
            log_path = evidence / f'{name}.log'
            print(f'Running {name}; output: {log_path}', flush=True)
            with log_path.open('x') as log:
                child = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                code = child.wait()
            outcomes.append({'stage': name, 'exit_code': code, 'log': str(log_path)})
            if code:
                raise RuntimeError(f'{name} failed with exit status {code}; inspect {log_path}')
        reports = sorted(evidence.glob('traffic_metrics_*.json'))
        if len(reports) != 2:
            raise RuntimeError(f'Expected two experiment reports, found {len(reports)}')
        for path in reports:
            verify_report(json.loads(path.read_text()))
        result = {'success': True, 'stages': outcomes, 'reports': [str(p) for p in reports]}
        code = 0
    except (Exception, KeyboardInterrupt) as exc:
        if child is not None and child.poll() is None:
            # The child handles SIGTERM through its normal cleanup path.
            child.terminate()
            child.wait()
        result = {'success': False, 'stages': outcomes,
                  'error': f'{type(exc).__name__}: {exc}'}
        code = 130 if isinstance(exc, KeyboardInterrupt) else 1
    with (evidence / 'verification.json').open('x') as output:
        json.dump(result, output, indent=2, allow_nan=False)
    print('Phase 3 live verification PASSED' if code == 0 else 'Phase 3 live verification FAILED')
    print(f'Evidence: {evidence}')
    return code


if __name__ == '__main__':
    sys.exit(main())
