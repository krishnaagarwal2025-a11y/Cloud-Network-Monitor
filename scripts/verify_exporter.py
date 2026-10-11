#!/usr/bin/env python3
"""Check standalone startup, exposition, optional existing scrape, and owned shutdown.

Uses an empty report directory: verifies readiness, never fabricates probe data.
"""
import argparse
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prometheus_client.parser import text_string_to_metric_families
from src.monitoring.prometheus import wait_query
from src.traffic.process import owns_listener, stop_process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9200)
    parser.add_argument('--prometheus-port', type=int, help='Verify an existing service scrapes this exporter')
    args = parser.parse_args()
    child = None
    with tempfile.TemporaryDirectory(prefix='exporter-verification-') as tmp:
        with tempfile.TemporaryFile(mode='w+b') as log:
            try:
                child = subprocess.Popen([sys.executable, str(ROOT / 'src/monitoring/exporter.py'),
                                          '--port', str(args.port), '--results-dir', tmp],
                                         cwd=tmp, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 10
                while not owns_listener(child.pid, args.port):
                    if child.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        raise RuntimeError(log.read().decode(errors='replace') or 'Startup timeout')
                    time.sleep(.05)
                with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{args.port}/metrics', timeout=3) as response:
                    assert response.status == 200
                    families = list(text_string_to_metric_families(response.read().decode()))
                assert any(f.name == 'cloud_collection_success' for f in families)
                print(f'PASS: owned listener 127.0.0.1:{args.port}; HTTP 200; valid exposition; no probe data')
                if args.prometheus_port:
                    wait_query(args.prometheus_port,
                               f'up{{job="cloud-network-monitor",instance="127.0.0.1:{args.port}"}}',
                               lambda rows: len(rows) == 1 and float(rows[0]['value'][1]) == 1)
                    print(f'PASS: existing Prometheus {args.prometheus_port} scraped exporter (up=1)')
            finally:
                if child is not None:
                    # The standalone exporter handles Ctrl+C through its server context.
                    if child.poll() is None:
                        child.send_signal(signal.SIGINT)
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            stop_process(child)
                    else:
                        child.wait()
                print('Owned exporter stopped; existing services preserved.')


if __name__ == '__main__':
    main()
