#!/usr/bin/env python3
"""Single acceptance entry point; portable checks or complete privileged live checks."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def commands(live):
    python = sys.executable
    checks = [
        ('tests', [python, '-m', 'unittest', 'discover', '-v', '-s', 'tests']),
        ('prometheus_config', ['promtool', 'check', 'config', 'config/prometheus.yml']),
        ('setup_syntax', ['bash', '-n', 'scripts/setup_env.sh']),
        ('whitespace', ['git', 'diff', '--check']),
    ]
    if live:
        checks.extend((f'phase{phase}', [python, f'scripts/verify_phase{phase}.py'])
                      for phase in (3, 4, 5, 6))
        checks.append(('end_to_end_live', [python, 'scripts/run_demo.py']))
    else:
        checks.extend([
            ('phase6_simulation', [python, 'scripts/verify_phase6.py', '--simulated']),
            ('end_to_end_simulation', [python, 'scripts/run_demo.py', '--simulated']),
        ])
    return checks


def stop_group(child):
    if child is not None and child.poll() is None:
        # Every stage owns its new session. This never targets unrelated processes.
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Requires root; actual network, faults and recovery')
    parser.add_argument('--stage-timeout', type=int, default=1200, help='Maximum seconds for each check')
    args = parser.parse_args()
    if args.stage_timeout < 1:
        parser.error('stage-timeout must be positive')
    if args.live and os.geteuid() != 0:
        parser.error('Live acceptance requires sudo; no check was executed')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    for name in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(name, interrupted)
    directory = ROOT / 'results' / f'acceptance_{time.time_ns()}'
    directory.mkdir(parents=True, exist_ok=False)
    result = {'success': False, 'mode': 'live_mininet' if args.live else 'portable_and_simulated',
              'stages': [], 'started_at': time.time(),
              'limitations': [] if args.live else ['Does not establish live Mininet or kernel fault acceptance.']}
    print(f'Acceptance logs: {directory}', flush=True)
    child = None
    status = 1
    try:
        for name, argv in commands(args.live):
            log_path = directory / f'{name}.log'
            start = time.monotonic()
            print(f'Running {name}: {log_path.name}', flush=True)
            with log_path.open('x') as log:
                child = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True)
                try:
                    code = child.wait(timeout=args.stage_timeout)
                except subprocess.TimeoutExpired:
                    stop_group(child)
                    raise RuntimeError(f'{name} exceeded its timeout; see {log_path}')
            result['stages'].append({'stage': name, 'exit_code': code, 'log': log_path.name,
                                      'duration_seconds': time.monotonic() - start})
            if code:
                raise RuntimeError(f'{name} failed with exit status {code}; see {log_path}')
        result['success'] = True
        status = 0
    except (Exception, KeyboardInterrupt) as exc:
        stop_group(child)
        result['error'] = f'{type(exc).__name__}: {exc}'
        print(result['error'], file=sys.stderr)
        status = 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        result['finished_at'] = time.time()
        with (directory / 'acceptance.json').open('x') as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
        print(f'{"PASS" if result["success"] else "FAILED"} ({result["mode"]}): {directory}', flush=True)
    return status


if __name__ == '__main__':
    sys.exit(main())
