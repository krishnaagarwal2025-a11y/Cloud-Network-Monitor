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
        from src.monitoring.grafana import query_panel
        from src.monitoring.prometheus import wait_query
        from scripts.verify_phase4 import free_port

        collector = MeasurementCollector()
        collector.update(json.loads((ROOT / 'tests/fixtures/simulated_baseline.json').read_text()))
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
                        wait_query(prom_port, 'cloud_measurement_cycle_success',
                                   lambda rows: len(rows) == 1 and float(rows[0]['value'][1]) == 1)
                        # Range panels need scrape history after their aligned start;
                        # a single instant sample can fall after the last range step.
                        wait_query(prom_port, 'count_over_time(cloud_measurement_cycle_success[5s])',
                                   lambda rows: len(rows) == 1 and float(rows[0]['value'][1]) >= 3)
                        import time
                        end = int(time.time() * 1000)
                        for uid in (DASHBOARD_UID, 'cloud-anomaly-detection'):
                            dashboard = api(grafana_port, f'/api/dashboards/uid/{uid}')
                            self.assertTrue(dashboard['meta']['provisioned'])
                            for panel in dashboard['dashboard']['panels']:
                                with self.subTest(uid=uid, panel=panel['title']):
                                    self.assertTrue(numbers(query_panel(grafana_port, panel, end - 60000, end)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
