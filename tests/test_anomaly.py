"""Unit tests for threshold, EWMA, and Z-score anomaly detectors."""

import unittest
from src.analysis.detector import (
    ThresholdDetector,
    EWMADetector,
    ZScoreDetector,
    CompositeAnomalyDetector,
    AnomalyRecord,
)


def make_report(latency=17.0, loss=0.0, http_time=35.0, http_status=200,
                throughput=92.0, success=True):
    return {
        'timestamp': 1000.0,
        'success': success,
        'icmp': [
            {'source': 'client', 'target': 'web_srv', 'success': success,
             'rtt_avg_ms': latency, 'packet_loss_percent': loss, 'timestamp': 1000.0},
            {'source': 'client', 'target': 'edge_gw', 'success': True,
             'rtt_avg_ms': 10.0, 'packet_loss_percent': 0.0, 'timestamp': 1000.0},
        ],
        'http': [
            {'source': 'client', 'target': 'web_srv', 'success': (http_status == 200),
             'http_status': http_status, 'response_time_ms': http_time, 'timestamp': 1000.0},
        ],
        'throughput': [
            {'source': 'client', 'target': 'web_srv', 'success': success,
             'throughput_mbps': throughput, 'timestamp': 1000.0},
        ],
    }


class TestThresholdDetector(unittest.TestCase):
    def setUp(self):
        self.detector = ThresholdDetector()

    def test_normal_baseline_produces_no_anomalies(self):
        report = make_report()
        anomalies = self.detector.evaluate_cycle(report)
        self.assertEqual(len(anomalies), 0)

    def test_latency_threshold_breach(self):
        report = make_report(latency=85.0)  # threshold is 40.0 for client->web_srv
        anomalies = self.detector.evaluate_cycle(report)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].metric, 'latency')
        self.assertEqual(anomalies[0].severity, 'critical')
        self.assertEqual(anomalies[0].source, 'client')
        self.assertEqual(anomalies[0].target, 'web_srv')

    def test_packet_loss_breach(self):
        report = make_report(loss=5.0)
        anomalies = self.detector.evaluate_cycle(report)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].metric, 'packet_loss')
        self.assertEqual(anomalies[0].severity, 'warning')

    def test_http_failure_breach(self):
        report = make_report(http_status=500, http_time=None)
        anomalies = self.detector.evaluate_cycle(report)
        self.assertTrue(any(a.metric == 'availability' for a in anomalies))

    def test_missing_data_does_not_trigger_false_latency_alert(self):
        report = make_report(latency=None, success=False)
        report['icmp'][0]['rtt_avg_ms'] = None
        anomalies = self.detector.evaluate_cycle(report)
        # Should not flag latency anomaly when latency is None
        self.assertFalse(any(a.metric == 'latency' for a in anomalies))

    def test_throughput_drop_breach(self):
        report = make_report(throughput=20.0)  # threshold is 50.0
        anomalies = self.detector.evaluate_cycle(report)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].metric, 'throughput')


class TestEWMADetector(unittest.TestCase):
    def setUp(self):
        self.detector = EWMADetector(alpha=0.3, k_factor=3.0, min_samples=3, min_delta=2.0)

    def test_warmup_produces_no_false_alerts(self):
        for i in range(3):
            report = make_report(latency=17.0)
            report['timestamp'] = 1000.0 + i
            anomalies = self.detector.evaluate_cycle(report)
            self.assertEqual(len(anomalies), 0)

    def test_detects_sudden_latency_spike(self):
        # Establish baseline
        for i in range(5):
            report = make_report(latency=17.0)
            report['timestamp'] = 1000.0 + i
            self.detector.evaluate_cycle(report)

        # Injected spike
        spike_report = make_report(latency=55.0)
        spike_report['timestamp'] = 1010.0
        anomalies = self.detector.evaluate_cycle(spike_report)
        lat_anomalies = [a for a in anomalies if a.metric == 'latency']
        self.assertEqual(len(lat_anomalies), 1)
        self.assertEqual(lat_anomalies[0].detector, 'ewma')
        self.assertGreater(lat_anomalies[0].observed_value, 50.0)

    def test_none_values_skipped_safely(self):
        key = ('latency', 'client', 'web_srv')
        self.assertIsNone(self.detector.update(key, None, 1000.0))
        self.assertIsNone(self.detector.update(key, float('nan'), 1000.0))


class TestZScoreDetector(unittest.TestCase):
    def setUp(self):
        self.detector = ZScoreDetector(window_size=10, z_threshold=3.0, min_samples=5, min_delta=2.0)

    def test_warmup_suppresses_alerts(self):
        for i in range(4):
            report = make_report(latency=17.0)
            report['timestamp'] = 1000.0 + i
            anomalies = self.detector.evaluate_cycle(report)
            self.assertEqual(len(anomalies), 0)

    def test_zero_variance_handled_gracefully(self):
        # Baseline with constant 17.0ms latency (variance = 0)
        for i in range(6):
            report = make_report(latency=17.0)
            report['timestamp'] = 1000.0 + i
            self.detector.evaluate_cycle(report)

        # Small noise within min_delta should not alert
        noise = make_report(latency=17.5)
        self.assertEqual(len(self.detector.evaluate_cycle(noise)), 0)

        # Significant spike exceeding min_delta should alert
        spike = make_report(latency=60.0)
        anomalies = self.detector.evaluate_cycle(spike)
        lat_anomalies = [a for a in anomalies if a.metric == 'latency']
        self.assertEqual(len(lat_anomalies), 1)
        self.assertEqual(lat_anomalies[0].detector, 'zscore')

    def test_detects_throughput_drop(self):
        for i in range(6):
            report = make_report(throughput=92.0)
            report['timestamp'] = 1000.0 + i
            self.detector.evaluate_cycle(report)

        drop = make_report(throughput=15.0)
        anomalies = self.detector.evaluate_cycle(drop)
        tp_anomalies = [a for a in anomalies if a.metric == 'throughput']
        self.assertEqual(len(tp_anomalies), 1)


class TestCompositeAnomalyDetector(unittest.TestCase):
    def setUp(self):
        self.detector = CompositeAnomalyDetector()

    def test_baseline_clean(self):
        report = make_report()
        result = self.detector.analyze_cycle(report)
        self.assertFalse(result['anomaly_detected'])
        self.assertEqual(result['total_anomalies'], 0)

    def test_impairment_detected_by_multiple_engines(self):
        for i in range(6):
            report = make_report(latency=17.0)
            report['timestamp'] = 1000.0 + i
            self.detector.analyze_cycle(report)

        impaired = make_report(latency=80.0, loss=15.0)
        result = self.detector.analyze_cycle(impaired)
        self.assertTrue(result['anomaly_detected'])
        self.assertGreater(result['total_anomalies'], 1)
        self.assertIn('latency', result['by_metric'])
        self.assertIn('packet_loss', result['by_metric'])
        self.assertIn('client->web_srv', result['by_path'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
