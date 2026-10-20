"""Owned localhost Grafana with file-provisioned dashboards and datasource."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.request import ProxyHandler, Request, build_opener

from src.traffic.process import owns_listener, stop_process

ROOT = Path(__file__).resolve().parents[2]
GRAFANA_HOME = ROOT / '.runtime' / 'grafana-13.2.3'
DASHBOARD_UID = 'cloud-network-overview'
DATASOURCE_UID = 'cloud-prometheus'


def api(port, path, payload=None):
    request = Request(f'http://127.0.0.1:{port}{path}',
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({})).open(request, timeout=10) as response:
        return json.load(response)


def query_panel(port, panel, start_ms, end_ms):
    queries = [{**target, 'datasource': {'type': 'prometheus', 'uid': DATASOURCE_UID},
                'intervalMs': 1000, 'maxDataPoints': 1000}
               for target in panel.get('targets', [])]
    return api(port, '/api/ds/query', {'queries': queries, 'from': str(start_ms), 'to': str(end_ms)})


def validate_existing(port, prometheus_port):
    """Refuse healthy but unrelated dashboards/data sources without changing them."""
    source = api(port, f'/api/datasources/uid/{DATASOURCE_UID}')
    if source.get('type') != 'prometheus' or source.get('url', '').rstrip('/') != f'http://127.0.0.1:{prometheus_port}':
        raise RuntimeError('Existing Grafana datasource does not point at the project Prometheus')
    for uid in (DASHBOARD_UID, 'cloud-anomaly-detection'):
        dashboard = api(port, f'/api/dashboards/uid/{uid}')
        if not dashboard.get('meta', {}).get('provisioned') or dashboard['dashboard'].get('uid') != uid:
            raise RuntimeError(f'Existing Grafana dashboard is missing or not provisioned: {uid}')


@contextmanager
def grafana_server(directory, prometheus_port=9091, port=3000, home=None):
    home = Path(home or GRAFANA_HOME).resolve()
    binary = home / 'bin' / 'grafana'
    if not binary.is_file():
        raise RuntimeError('Grafana is missing; approve and run scripts/install_grafana.py first')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.update({
        'GF_PATHS_DATA': str(directory / 'data'),
        'GF_PATHS_LOGS': str(directory / 'logs'),
        'GF_PATHS_PLUGINS': str(directory / 'plugins'),
        'GF_PATHS_PROVISIONING': str(ROOT / 'config/grafana/provisioning'),
        'GF_SERVER_HTTP_ADDR': '127.0.0.1', 'GF_SERVER_HTTP_PORT': str(port),
        'GF_AUTH_ANONYMOUS_ENABLED': 'true', 'GF_AUTH_ANONYMOUS_ORG_ROLE': 'Viewer',
        'GF_AUTH_DISABLE_LOGIN_FORM': 'true', 'GF_SECURITY_ADMIN_PASSWORD': secrets.token_urlsafe(32),
        'GF_ANALYTICS_REPORTING_ENABLED': 'false', 'GF_ANALYTICS_CHECK_FOR_UPDATES': 'false',
        'GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES': 'false', 'GF_PLUGINS_PREINSTALL_DISABLED': 'true',
        'GF_NEWS_NEWS_FEED_ENABLED': 'false', 'GF_LOG_MODE': 'console',
        'CLOUD_PROMETHEUS_URL': f'http://127.0.0.1:{prometheus_port}',
        'CLOUD_DASHBOARDS_DIR': str(ROOT / 'config/grafana/dashboards'),
    })
    process = None
    with (directory / 'grafana.log').open('x') as log:
        try:
            process = subprocess.Popen([str(binary), 'server', '--homepath', str(home)],
                                       env=env, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'Grafana exited; inspect {directory / "grafana.log"}')
                try:
                    if owns_listener(process.pid, port):
                        dashboard = api(port, f'/api/dashboards/uid/{DASHBOARD_UID}')
                        if dashboard['dashboard']['uid'] == DASHBOARD_UID:
                            break
                except (OSError, ValueError, KeyError):
                    time.sleep(0.2)
            else:
                raise RuntimeError(f'Grafana readiness timeout; inspect {directory / "grafana.log"}')
            yield process
        finally:
            if process is not None:
                stop_process(process)
