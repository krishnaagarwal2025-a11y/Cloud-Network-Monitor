"""Focused regressions for startup, inference and partial fault failures."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from src.analysis.detector import ThresholdDetector, EWMADetector, ZScoreDetector
from src.analysis.localiser import FaultLocaliser
from src.monitoring.exporter import MeasurementCollector, load_report
from src.traffic.fault_injection import FaultInjector, inject_fault

ROOT = Path(__file__).resolve().parents[1]


class TestAuditRegressions(unittest.TestCase):
    def test_exporter_direct_entry_from_other_directory(self):
        result = subprocess.run([sys.executable, str(ROOT / 'src/monitoring/exporter.py'), '--help'],
                                cwd='/tmp', capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--results-dir', result.stdout)

    def test_corrupt_report_clears_previous_snapshot_with_diagnostic(self):
        collector = MeasurementCollector()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'report.json'
            report.write_text((ROOT / 'tests/fixtures/simulated_baseline.json').read_text())
            self.assertTrue(load_report(collector, report))
            report.write_text('{bad json')
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertFalse(load_report(collector, report))
            self.assertIn('JSONDecodeError', output.getvalue())
            self.assertIsNone(collector._report)
            self.assertEqual(collector._errors, 1)

    def test_no_observations_are_inconclusive(self):
        result = FaultLocaliser().localise({'icmp': []})
        self.assertEqual(result.status, 'inconclusive')
        self.assertEqual(result.eliminated_segments, [])
        self.assertEqual(result.confidence, 0)
        self.assertFalse(result.evaluate_ground_truth(None)['matched'])

    def test_application_error_does_not_identify_network_segment(self):
        report = json.loads((ROOT / 'tests/fixtures/simulated_baseline.json').read_text())
        report['http'][0].update(success=False, http_status=500)
        from src.analysis.detector import CompositeAnomalyDetector
        anomalies = CompositeAnomalyDetector().analyze_cycle(report)
        self.assertTrue(anomalies['anomaly_detected'])
        result = FaultLocaliser().localise(report, anomalies)
        self.assertEqual(result.status, 'healthy')
        self.assertEqual(result.candidate_segments, [])

    def test_invalid_detector_parameters(self):
        for constructor, kwargs in ((ThresholdDetector, {'thresholds': {'http_response_time_ms': 0}}),
                                    (ThresholdDetector, {'thresholds': {'packet_loss_ratio': 2}}),
                                    (EWMADetector, {'min_delta': 0}),
                                    (EWMADetector, {'k_factor': float('nan')}),
                                    (ZScoreDetector, {'min_samples': 1}),
                                    (ZScoreDetector, {'min_delta': 0}),
                                    (ZScoreDetector, {'window_size': 3, 'min_samples': 5})):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                constructor(**kwargs)

    def test_invalid_localiser_thresholds(self):
        for kwargs in ({'latency_spike_threshold_ms': 0}, {'loss_threshold_percent': float('nan')},
                       {'loss_threshold_percent': 101}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FaultLocaliser(**kwargs)

    def test_wrong_existing_prometheus_target_prevents_network_startup(self):
        from contextlib import nullcontext
        from unittest.mock import patch
        from scripts.run_monitoring import run
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(io.StringIO()), \
             patch('scripts.run_monitoring.metrics_server', return_value=nullcontext(9200)), \
             patch('src.monitoring.prometheus.get_json', side_effect=[{}, {'data': {'activeTargets': []}}]), \
             patch('scripts.run_monitoring.experiment_network') as network:
            directory = Path(tmp) / 'session'
            self.assertEqual(run(cycles=1, output_dir=directory), 1)
            network.assert_not_called()
            session = json.loads((directory / 'session.json').read_text())
            self.assertIn('does not scrape', session['error'])

    def test_invalid_fault_parameters_never_touch_interfaces(self):
        injector = FaultInjector(Mock())
        injector.find_segment_link = Mock()
        for kwargs in ({}, {'loss': -1}, {'loss': 101}, {'bw': 0}, {'bw': float('nan')},
                       {'delay': '1ms; arbitrary-command'}, {'loss': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                injector.apply_fault('seg_client_edge', **kwargs)
        injector.find_segment_link.assert_not_called()

    def test_partial_apply_failure_restores_both_interfaces(self):
        one, two = Mock(name='one'), Mock(name='two')
        one.name, two.name = 's_client-eth2', 's_edge-eth2'
        one.config.return_value = {'tcoutputs': ['']}
        two.config.side_effect = [{'tcoutputs': ['RTNETLINK error']}, {'tcoutputs': ['']}]
        one.cmd.return_value = two.cmd.return_value = 'qdisc htb root\nqdisc netem delay 5ms'
        injector = FaultInjector(Mock(links=[Mock(intf1=one, intf2=two)]))
        injector.find_segment_link = Mock(return_value=Mock(intf1=one, intf2=two))
        with self.assertRaisesRegex(RuntimeError, 'tc configuration failed'):
            injector.apply_fault('seg_client_edge', delay='50ms')
        self.assertEqual(one.config.call_count, 2)
        self.assertEqual(two.config.call_count, 2)
        self.assertEqual(one.config.call_args.kwargs['delay'], '5ms')
        self.assertEqual(injector.active_faults, {})

    def test_context_exception_cleans_actual_injector(self):
        retained = None
        with self.assertRaises(ZeroDivisionError):
            with inject_fault(None, 'seg_client_edge', delay='50ms') as injector:
                retained = injector
                raise ZeroDivisionError()
        self.assertEqual(retained.active_faults, {})

    def test_restore_failure_retains_ownership_and_attempts_both_interfaces(self):
        injector = FaultInjector(None)
        injector.apply_fault('seg_client_edge', delay='50ms')
        one, two = Mock(), Mock()
        one.config.return_value = {'tcoutputs': ['restoration error']}
        two.config.return_value = {'tcoutputs': ['']}
        two.cmd.return_value = 'qdisc htb root\nqdisc netem delay 5ms'
        injector.find_segment_link = Mock(return_value=Mock(intf1=one, intf2=two))
        with self.assertRaisesRegex(RuntimeError, 'Restoration failed'):
            injector.restore_fault('seg_client_edge')
        one.config.assert_called_once()
        two.config.assert_called_once()
        self.assertIn('seg_client_edge', injector.active_faults)

    def test_malformed_ping_statistics_do_not_become_observations(self):
        from src.traffic.icmp import parse_ping_output
        report = parse_ping_output('0 packets transmitted, 2 received, 101% packet loss\n'
                                   'rtt min/avg/max/mdev = 20/10/5/1 ms')
        self.assertFalse(report['success'])
        self.assertIsNone(report['packet_loss_percent'])
        self.assertIsNone(report['rtt_avg_ms'])

    def test_negative_iperf_optional_statistic_is_a_failure(self):
        from src.traffic.iperf import parse_iperf_json
        report = parse_iperf_json('{"end":{"sum_received":{"bits_per_second":90000000,"seconds":2},'
                                  '"sum_sent":{"retransmits":-1}}}')
        self.assertFalse(report['success'])
        self.assertIsNone(report['throughput_mbps'])
