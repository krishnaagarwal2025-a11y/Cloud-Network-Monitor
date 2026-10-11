"""Dashboard provisioning contract; real rendering is checked with Grafana."""

import json
from pathlib import Path
import unittest

from scripts.verify_phase5 import numbers
from src.monitoring.grafana import DASHBOARD_UID, DATASOURCE_UID

ROOT = Path(__file__).resolve().parents[1]


class TestDashboard(unittest.TestCase):
    def test_all_panels_use_real_metrics_and_provisioned_datasource(self):
        dashboard = json.loads((ROOT / 'config/grafana/dashboards/network-overview.json').read_text())
        self.assertEqual(dashboard['uid'], DASHBOARD_UID)
        self.assertEqual(dashboard['refresh'], '5s')
        ids = set()
        for panel in dashboard['panels']:
            self.assertNotIn(panel['id'], ids)
            ids.add(panel['id'])
            self.assertEqual(panel['datasource']['uid'], DATASOURCE_UID)
            for target in panel['targets']:
                self.assertTrue('cloud_' in target['expr'] or target['expr'].startswith('up{'))
                self.assertNotIn('vector(', target['expr'])
        self.assertGreaterEqual(len(ids), 10)

    def test_anomaly_dashboard_contract(self):
        dashboard = json.loads((ROOT / 'config/grafana/dashboards/anomaly-detection.json').read_text())
        self.assertEqual(dashboard['uid'], 'cloud-anomaly-detection')
        ids = set()
        for panel in dashboard['panels']:
            self.assertNotIn(panel['id'], ids)
            ids.add(panel['id'])
            self.assertEqual(panel['datasource']['uid'], DATASOURCE_UID)
            for target in panel['targets']:
                self.assertTrue('cloud_' in target['expr'])
        self.assertGreaterEqual(len(ids), 5)


    def test_live_monitoring_stack_startup_and_reachability(self):
        from tempfile import TemporaryDirectory
        from urllib.request import urlopen
        from src.monitoring.exporter import MeasurementCollector, metrics_server
        from src.monitoring.prometheus import prometheus_server
        from src.monitoring.grafana import grafana_server, api, DASHBOARD_UID
        from scripts.verify_phase4 import free_port

        collector = MeasurementCollector()
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            exporter_port = free_port()
            prom_port = free_port()
            grafana_port = free_port()

            with metrics_server(collector, exporter_port):
                with prometheus_server(tmp_path / 'prom', exporter_port, prom_port):
                    with grafana_server(tmp_path / 'grafana', prom_port, grafana_port):
                        # 1. Verify Exporter reachability
                        with urlopen(f'http://127.0.0.1:{exporter_port}/metrics') as resp:
                            self.assertEqual(resp.status, 200)
                            content = resp.read().decode()
                            self.assertIn('cloud_collection_success', content)

                        # 2. Verify Prometheus reachability
                        with urlopen(f'http://127.0.0.1:{prom_port}/-/ready') as resp:
                            self.assertEqual(resp.status, 200)

                        # 3. Verify Grafana health and provisioned dashboard
                        health = api(grafana_port, '/api/health')
                        self.assertEqual(health.get('database'), 'ok')

                        dashboard = api(grafana_port, f'/api/dashboards/uid/{DASHBOARD_UID}')
                        self.assertEqual(dashboard['dashboard']['uid'], DASHBOARD_UID)


if __name__ == '__main__':
    unittest.main(verbosity=2)
