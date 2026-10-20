"""Regression tests for configuration, experiment evidence and kernel verification."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.run_demo import read_config, synthetic_cycle
from scripts.verify_all import commands
from src.analysis.configuration import load_analysis
from src.analysis.detector import CompositeAnomalyDetector
from src.analysis.evaluation import evaluate
from src.analysis.reporting import line_chart, write_report
from src.monitoring.exporter import MeasurementCollector
from src.traffic.fault_injection import FaultInjector
from src.monitoring.grafana import validate_existing

ROOT = Path(__file__).resolve().parents[1]


class TestFinalAcceptance(unittest.TestCase):
    def setUp(self):
        self.base = json.loads((ROOT / 'tests/fixtures/simulated_baseline.json').read_text())
        self.config = read_config(ROOT / 'config/experiment.json')

    def test_configuration_reaches_detector_and_invalid_paths_fail(self):
        settings = json.loads((ROOT / 'config/analysis.json').read_text())
        settings['thresholds']['latency_ms']['client->web_srv'] = 10
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps(settings))
            detector, _, _ = load_analysis(path)
            result = detector.analyze_cycle(self.base)
            self.assertTrue(any(a['metric'] == 'latency' and a['target'] == 'web_srv' for a in result['anomalies']))
            settings['thresholds']['latency_ms']['unknown->host'] = 20
            path.write_text(json.dumps(settings))
            with self.assertRaisesRegex(ValueError, 'Unknown latency path'):
                load_analysis(path)

    def test_http_repetitions_do_not_accelerate_statistical_warmup(self):
        detector = CompositeAnomalyDetector()
        report = deepcopy(self.base)
        report['http'] = [deepcopy(report['http'][0]) for _ in range(10)]
        detector.analyze_cycle(report)
        key = ('http_response_time', 'client', 'web_srv')
        self.assertEqual(detector.ewma_detector.series[key]['count'], 1)
        self.assertEqual(len(detector.zscore_detector.windows[key]), 1)

    def test_invalid_experiment_rejected_before_effects(self):
        for field, value in [('baseline_cycles', 1), ('fault_cycles', 0), ('icmp_count', True)]:
            config = deepcopy(self.config)
            config[field] = value
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'config.json'
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    read_config(path)

    def test_synthetic_loss_is_consistent_and_does_not_mutate_baseline(self):
        original = deepcopy(self.base)
        scenario = next(s for s in self.config['scenarios'] if s['condition'] == 'loss')
        report = synthetic_cycle(self.base, scenario)
        self.assertEqual(self.base, original)
        self.assertEqual(report['measurement_mode'], 'synthetic_simulation')
        for probe in report['icmp']:
            if probe['target'] == 'db_srv':
                self.assertEqual(probe['packet_loss_percent'], 100)
                self.assertIsNone(probe['rtt_avg_ms'])
                self.assertFalse(probe['success'])
        self.assertIsNone(next(p for p in report['throughput'] if p['target'] == 'db_srv')['throughput_mbps'])

    def test_injection_labels_do_not_determine_inferred_candidate(self):
        collector = MeasurementCollector()
        collector.experiment_state('latency', 'seg_client_edge', applied=True)
        collector.update(self.base)
        self.assertEqual(collector.analysis_snapshot()['localisation']['status'], 'healthy')
        self.assertEqual(collector.analysis_snapshot()['localisation']['candidate_segments'], [])

    def test_actual_tc_parameter_parsing_and_mismatch_detection(self):
        qdisc = 'qdisc htb 5: root default 0x1\nqdisc netem 10: parent 5:1 limit 1000 delay 5000us loss 5%'
        parsed = FaultInjector.parse_state(qdisc, 'class htb 5:1 root rate 1Gbit ceil 1Gbit')
        self.assertEqual(parsed, dict(delay_ms=5, loss=5, bw=1000, max_queue_size=1000))
        injector = FaultInjector(Mock())
        injector.snapshot_segment = Mock(return_value={'mode': 'live_kernel', 'interfaces': [
            {'interface': 's_edge-eth2', 'parameters': parsed}]})
        with self.assertRaisesRegex(RuntimeError, 'Actual loss'):
            injector.verify_segment('seg_edge_lb', {'delay': '5ms', 'loss': 0, 'bw': 1000})
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            FaultInjector.parse_state('qdisc fq_codel root', '')

    def test_evaluation_preserves_false_results_and_undefined_rates(self):
        def cycle(condition, detected, timestamp, warmup=False):
            return {'condition': condition, 'scenario': 'test', 'segment': None,
                    'timestamp': timestamp, 'warmup': warmup,
                    'analysis': {'anomalies': {'by_detector': {name: detected for name in ('threshold', 'ewma', 'zscore')}},
                                 'localisation': {'status': 'healthy', 'candidate_segments': []}}}
        records = [cycle('baseline', 1, 1, True), cycle('baseline', 1, 2),
                   cycle('latency', 0, 3), cycle('latency', 1, 5), cycle('recovery', 0, 6)]
        result = evaluate(records, [{'scenario': 'test', 'state': 'applied', 'timestamp': 2.5}])
        for stats in result['detectors'].values():
            self.assertEqual([stats[k] for k in ('true_positive', 'false_positive', 'true_negative', 'false_negative')], [1,1,1,1])
            self.assertEqual(stats['detection_delay_seconds']['test'], 2.5)
        self.assertIsNone(evaluate([], [])['detectors']['threshold']['precision'])

    def test_chart_missing_values_are_gaps_and_mode_is_visible(self):
        svg = line_chart('Latency', 'ms', {'path': [1, 2, None, 3, 4]}, ['baseline']*5, 'synthetic_simulation')
        self.assertEqual(svg.count('<polyline'), 2)
        self.assertIn('synthetic_simulation', svg)
        self.assertIn('Collection cycle', svg)

    def test_failure_report_and_empty_charts_remain_honest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            experiment = {'success': False, 'mode': 'synthetic_simulation', 'cycles': [], 'events': [],
                          'settings': {'scenarios': []}, 'evaluation': evaluate([], [])}
            (path / 'experiment.json').write_text(json.dumps(experiment))
            write_report(path)
            self.assertIn('FAILED / INCOMPLETE', (path / 'summary.md').read_text())
            self.assertIn('synthetic_simulation', (path / 'report.html').read_text())
            self.assertTrue((path / 'latency.svg').exists())

    def test_portable_acceptance_does_not_invoke_privileged_stages(self):
        portable = commands(False)
        self.assertTrue(any('--simulated' in argv for _, argv in portable))
        self.assertFalse(any(name == 'end_to_end_live' for name, _ in portable))
        self.assertTrue(any(name == 'end_to_end_live' for name, _ in commands(True)))

    def test_existing_grafana_cannot_silently_reuse_wrong_prometheus(self):
        with patch('src.monitoring.grafana.api', return_value={
                'type': 'prometheus', 'url': 'http://127.0.0.1:9999'}):
            with self.assertRaisesRegex(RuntimeError, 'does not point'):
                validate_existing(3000, 9091)

    def test_existing_grafana_requires_both_provisioned_dashboards(self):
        with patch('src.monitoring.grafana.api', side_effect=[
                {'type': 'prometheus', 'url': 'http://127.0.0.1:9091'},
                {'meta': {'provisioned': False}, 'dashboard': {'uid': 'cloud-network-overview'}}]):
            with self.assertRaisesRegex(RuntimeError, 'not provisioned'):
                validate_existing(3000, 9091)

    def test_historical_import_never_claims_fresh_live_provenance(self):
        from src.monitoring.exporter import load_report
        report = deepcopy(self.base)
        report['measurement_mode'] = 'live_mininet'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'report.json'
            path.write_text(json.dumps(report))
            collector = MeasurementCollector()
            self.assertTrue(load_report(collector, path))
            self.assertEqual(collector._report['measurement_mode'], 'recorded_or_unspecified')

    def test_plain_observation_does_not_fabricate_fault_ground_truth(self):
        from prometheus_client import generate_latest
        collector = MeasurementCollector()
        collector.update(self.base)
        text = generate_latest(collector.registry).decode()
        self.assertNotIn('cloud_injected_fault_active', text)
        self.assertNotIn('cloud_experiment_condition', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
