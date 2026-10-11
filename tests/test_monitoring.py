"""Synthetic unit fixtures and real loopback tests for the metrics endpoint."""

from copy import deepcopy
import socket
import unittest
from urllib.request import ProxyHandler, build_opener

from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from src.monitoring.exporter import MeasurementCollector, metrics_server


def fixture():
    """Test-only records, never saved as experiment evidence."""
    return {
        'success': True,
        'icmp': [{'source': 'client', 'target': 'web_srv', 'success': True,
                  'timestamp': 100, 'rtt_avg_ms': 20, 'rtt_min_ms': 19,
                  'rtt_max_ms': 21, 'rtt_mdev_ms': 1, 'packet_loss_percent': 0}],
        'http': [{'source': 'client', 'target': 'web_srv', 'success': True,
                  'timestamp': 101, 'http_status': 200, 'response_time_ms': 40,
                  'connect_time_ms': 20}],
        'throughput': [{'source': 'client', 'target': 'web_srv', 'success': True,
                        'timestamp': 102, 'throughput_mbps': 90, 'duration_sec': 2,
                        'mean_rtt_ms': None, 'retransmits': 0}],
    }


def samples(collector):
    result = {}
    for family in text_string_to_metric_families(generate_latest(collector.registry).decode()):
        for sample in family.samples:
            result[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return result


def value(collector, name, **labels):
    return samples(collector).get((name, tuple(sorted(labels.items()))))


class TestMeasurementCollector(unittest.TestCase):
    def setUp(self):
        self.collector = MeasurementCollector()
        self.report = fixture()

    def test_no_measurements_before_first_cycle(self):
        self.assertEqual(value(self.collector, 'cloud_collection_success'), 0)
        self.assertIsNone(value(self.collector, 'cloud_ping_latency_seconds',
                                source='client', target='web_srv'))

    def test_units_timestamps_and_optional_values(self):
        self.collector.update(self.report, 2.5)
        path = dict(source='client', target='web_srv')
        self.assertEqual(value(self.collector, 'cloud_ping_latency_seconds', **path), 0.02)
        self.assertEqual(value(self.collector, 'cloud_throughput_bits_per_second', **path), 90000000)
        self.assertEqual(value(self.collector, 'cloud_http_response_seconds', **path), 0.04)
        self.assertEqual(value(self.collector, 'cloud_probe_timestamp_seconds', probe='http', **path), 101)
        self.assertIsNone(value(self.collector, 'cloud_tcp_rtt_seconds', **path))
        self.assertEqual(value(self.collector, 'cloud_collection_duration_seconds'), 2.5)

    def test_failed_http_does_not_leave_previous_success_or_latency(self):
        self.collector.update(self.report)
        self.report['success'] = False
        self.report['http'][0].update(success=False, http_status=0, response_time_ms=None)
        self.collector.update(self.report)
        path = dict(source='client', target='web_srv')
        self.assertEqual(value(self.collector, 'cloud_collection_success'), 1)
        self.assertEqual(value(self.collector, 'cloud_probe_success', probe='http', **path), 0)
        self.assertEqual(value(self.collector, 'cloud_http_success_ratio', **path), 0)
        self.assertIsNone(value(self.collector, 'cloud_http_response_seconds', **path))

    def test_full_packet_loss_remains_visible_without_latency(self):
        self.report['icmp'][0].update(success=False, packet_loss_percent=100, rtt_avg_ms=None)
        self.collector.update(self.report)
        path = dict(source='client', target='web_srv')
        self.assertEqual(value(self.collector, 'cloud_packet_loss_ratio', **path), 1)
        self.assertIsNone(value(self.collector, 'cloud_ping_latency_seconds', **path))

    def test_mixed_http_results_are_not_reported_as_full_availability(self):
        failure = deepcopy(self.report['http'][0])
        failure.update(success=False, http_status=500, timestamp=103)
        self.report['http'].append(failure)
        self.collector.update(self.report)
        path = dict(source='client', target='web_srv')
        self.assertEqual(value(self.collector, 'cloud_http_success_ratio', **path), 0.5)
        self.assertEqual(value(self.collector, 'cloud_http_status_code', **path), 500)
        self.assertIsNone(value(self.collector, 'cloud_http_response_seconds', **path))

    def test_exception_clears_previous_values_and_counts_error(self):
        self.collector.update(self.report)
        self.collector.collection_failed(3)
        self.assertEqual(value(self.collector, 'cloud_collection_success'), 0)
        self.assertEqual(value(self.collector, 'cloud_collection_errors_total'), 1)
        self.assertIsNone(value(self.collector, 'cloud_probe_success', probe='http',
                                source='client', target='web_srv'))

    def test_snapshot_is_immutable_and_old_paths_disappear(self):
        self.collector.update(self.report)
        self.report['icmp'][0]['rtt_avg_ms'] = 999
        path = dict(source='client', target='web_srv')
        self.assertEqual(value(self.collector, 'cloud_ping_latency_seconds', **path), 0.02)
        self.report['icmp'] = []
        self.collector.update(self.report)
        self.assertIsNone(value(self.collector, 'cloud_ping_latency_seconds', **path))

    def test_rejects_unbounded_labels_and_nonfinite_values(self):
        for field, bad in [('source', 'arbitrary-user-input'), ('timestamp', float('nan')),
                           ('rtt_avg_ms', float('inf'))]:
            report = fixture()
            report['icmp'][0][field] = bad
            with self.assertRaises(ValueError):
                self.collector.update(report)
        self.assertEqual(value(self.collector, 'cloud_collection_success'), 0)

    def test_anomaly_and_fault_metrics_clean_and_impaired(self):
        # 1. Clean report
        self.collector.update(self.report)
        self.assertEqual(value(self.collector, 'cloud_anomaly_detected'), 0)
        self.assertEqual(value(self.collector, 'cloud_anomalies_total'), 0)
        self.assertEqual(value(self.collector, 'cloud_fault_detected'), 0)
        self.assertEqual(value(self.collector, 'cloud_fault_candidate', segment='seg_client_edge'), 0)

        # 2. Impaired report (latency spike on client->web_srv)
        impaired = deepcopy(self.report)
        impaired['icmp'][0]['rtt_avg_ms'] = 95.0
        self.collector.update(impaired)
        self.assertEqual(value(self.collector, 'cloud_anomaly_detected'), 1)
        self.assertGreater(value(self.collector, 'cloud_anomalies_total'), 0)
        self.assertEqual(value(self.collector, 'cloud_fault_detected'), 1)



class TestMetricsEndpoint(unittest.TestCase):
    def test_real_scrape_and_port_cleanup(self):
        collector = MeasurementCollector()
        collector.update(fixture())
        with metrics_server(collector, 0) as port:
            with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/metrics', timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b'cloud_ping_latency_seconds{source="client",target="web_srv"} 0.02',
                              response.read())
        with socket.socket() as sock:
            self.assertNotEqual(sock.connect_ex(('127.0.0.1', port)), 0)

    def test_existing_listener_is_not_replaced(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            sock.listen()
            with self.assertRaises(OSError):
                with metrics_server(MeasurementCollector(), sock.getsockname()[1]):
                    self.fail('An occupied port must fail')
            self.assertEqual(sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
