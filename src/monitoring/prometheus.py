"""Run an owned Prometheus process without modifying an installed service."""

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import ProxyHandler, build_opener

from src.traffic.process import stop_process, owns_listener


def get_json(url):
    with build_opener(ProxyHandler({})).open(url, timeout=3) as response:
        return json.load(response)


def query(port, expression):
    result = get_json(f'http://127.0.0.1:{port}/api/v1/query?' + urlencode({'query': expression}))
    if result.get('status') != 'success':
        raise RuntimeError(f'Prometheus query failed: {result}')
    return result['data']['result']


def wait_query(port, expression, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = query(port, expression)
            if predicate(last):
                return last
        except (OSError, ValueError) as exc:
            last = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f'Prometheus verification timed out for {expression}: {last}')


@contextmanager
def prometheus_server(directory, exporter_port=9200, port=9091):
    """Separate short-retention data directory and loopback ports for the lab."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / 'prometheus.yml'
    with config.open('x') as stream:
        stream.write('global:\n  scrape_interval: 1s\n  scrape_timeout: 1s\n'
                     'scrape_configs:\n  - job_name: cloud-network-monitor\n'
                     f'    static_configs:\n      - targets: ["127.0.0.1:{exporter_port}"]\n')
    process = None
    with (directory / 'prometheus.log').open('x') as log:
        try:
            process = subprocess.Popen([
                'prometheus', f'--config.file={config}',
                f'--storage.tsdb.path={directory / "data"}', '--storage.tsdb.retention.time=24h',
                f'--web.listen-address=127.0.0.1:{port}',
            ], stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'Prometheus exited; inspect {directory / "prometheus.log"}')
                try:
                    if owns_listener(process.pid, port):
                        get_json(f'http://127.0.0.1:{port}/api/v1/status/buildinfo')
                        break
                except (OSError, ValueError):
                    time.sleep(0.1)
            else:
                raise RuntimeError('Prometheus did not become ready')
            yield process
        finally:
            if process is not None:
                stop_process(process)
