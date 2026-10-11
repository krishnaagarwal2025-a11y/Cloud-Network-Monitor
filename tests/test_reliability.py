"""Phase 3 failure, process ownership, and measurement integrity regressions."""
from contextlib import contextmanager
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.traffic.process import start_service, run_probe, owns_listener
from src.traffic.http import start_http_server, stop_http_server, measure_http
from src.traffic.iperf import start_iperf_server, stop_iperf_server, parse_iperf_json
from src.traffic.icmp import parse_ping_output
from src.traffic.manager import TrafficManager
from scripts.run_traffic_test import run_traffic_experiment, save_results
from src.topology.lifecycle import experiment_network


class LocalHost:
    name = 'local-test'

    def __init__(self, ip='127.0.0.1'):
        self.ip = ip
        self.children = []

    def IP(self):
        return self.ip

    def popen(self, argv, **kwargs):
        child = subprocess.Popen(argv, **kwargs)
        self.children.append(child)
        return child


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class TestOwnedProcesses(unittest.TestCase):
    """Real unprivileged local subprocesses; these are not Mininet live tests."""

    def test_two_iperf_servers_same_port_do_not_kill_each_other(self):
        port = free_port()
        hosts = [LocalHost('127.0.0.1'), LocalHost('127.0.0.2')]
        services = []
        try:
            for host in hosts:
                services.append(start_iperf_server(host, port))
            for service in services:
                service.assert_ready()
            services[0].stop()
            services[1].assert_ready()
        finally:
            for service in services:
                service.stop()
        self.assertTrue(all(s.process.poll() is not None for s in services))

    def test_http_repeated_runs_and_conflicting_listener_preserved(self):
        host = LocalHost()
        port = free_port()
        for _ in range(2):
            service = start_http_server(host, port)
            try:
                result = measure_http(host, f'http://127.0.0.1:{port}/')
                self.assertTrue(result.success, result.error_message)
                self.assertEqual(result.http_status, 200)
                with self.assertRaisesRegex(RuntimeError, 'failed to start'):
                    start_http_server(host, port)
                service.assert_ready()
            finally:
                stop_http_server(host, service)
            self.assertFalse(owns_listener(service.pid, port))
            self.assertTrue(all(p.poll() is not None for p in host.children))

    def test_start_failure_captures_diagnostic_and_reaps(self):
        host = LocalHost()
        with self.assertRaisesRegex(RuntimeError, 'intentional startup failure'):
            start_service(host, [sys.executable, '-c',
                          'import sys; sys.exit("intentional startup failure")'], 12345)
        self.assertIsNotNone(host.children[0].poll())

    def test_readiness_timeout_reaps_child(self):
        host = LocalHost()
        with self.assertRaisesRegex(RuntimeError, 'readiness timeout'):
            start_service(host, [sys.executable, '-c', 'import time; time.sleep(10)'],
                          12345, timeout=0.1)
        self.assertIsNotNone(host.children[0].poll())

    def test_probe_timeout_reaps_child(self):
        host = LocalHost()
        _, code, error = run_probe(host, [sys.executable, '-c',
                                  'import time; time.sleep(10)'], 0.1)
        self.assertIn('timed out', error)
        self.assertNotEqual(code, 0)
        self.assertIsNotNone(host.children[0].poll())

    def test_interrupt_reaps_probe(self):
        process = Mock()
        process.poll.side_effect = [None]
        process.communicate.side_effect = KeyboardInterrupt
        host = Mock()
        host.popen.return_value = process
        with self.assertRaises(KeyboardInterrupt):
            run_probe(host, ['probe'], 1)
        process.terminate.assert_called_once()
        process.wait.assert_called_once()


class TestFailureHandling(unittest.TestCase):
    def test_failed_network_build_still_attempts_owned_teardown(self):
        net = Mock(hosts=[], switches=[])
        net.build.side_effect = RuntimeError('build failed')
        state = {}
        with tempfile.TemporaryDirectory() as directory, \
             patch('src.topology.lifecycle.tempfile.gettempdir', return_value=directory), \
             patch('src.topology.lifecycle.os.geteuid', return_value=0), \
             patch('src.topology.lifecycle.preflight') as preflight, \
             patch('src.topology.lifecycle.Mininet', return_value=net):
            with self.assertRaisesRegex(RuntimeError, 'build failed'):
                with experiment_network(state):
                    self.fail('A failed build must not yield a network')
        net.stop.assert_called_once()
        self.assertEqual(preflight.call_count, 2)
        self.assertTrue(state['cleanup_success'])

    def test_network_conflict_does_not_create_or_delete_resources(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('src.topology.lifecycle.tempfile.gettempdir', return_value=directory), \
             patch('src.topology.lifecycle.os.geteuid', return_value=0), \
             patch('src.topology.lifecycle.preflight', side_effect=RuntimeError('conflict')), \
             patch('src.topology.lifecycle.Mininet') as constructor:
            with self.assertRaisesRegex(RuntimeError, 'conflict'):
                with experiment_network():
                    self.fail('A conflict must prevent startup')
            constructor.assert_not_called()

    def test_network_teardown_failure_is_not_reported_clean(self):
        net = Mock(hosts=[], switches=[])
        net.stop.side_effect = RuntimeError('stop failed')
        state = {}
        with tempfile.TemporaryDirectory() as directory, \
             patch('src.topology.lifecycle.tempfile.gettempdir', return_value=directory), \
             patch('src.topology.lifecycle.os.geteuid', return_value=0), \
             patch('src.topology.lifecycle.preflight'), \
             patch('src.topology.lifecycle.Mininet', return_value=net):
            with self.assertRaisesRegex(RuntimeError, 'stop failed'):
                with experiment_network(state):
                    pass
        self.assertFalse(state['cleanup_success'])

    def test_partial_startup_rolls_back_all_owned_services(self):
        manager = TrafficManager(Mock())
        http, iperf = Mock(), Mock()
        with patch('src.traffic.manager.start_http_server', return_value=http), \
             patch('src.traffic.manager.start_iperf_server', side_effect=[iperf, RuntimeError('bind failed')]), \
             patch('src.traffic.manager.stop_http_server') as stop_http, \
             patch('src.traffic.manager.stop_iperf_server') as stop_iperf:
            with self.assertRaisesRegex(RuntimeError, 'bind failed'):
                manager.setup_services()
            self.assertIs(stop_http.call_args.args[1], http)
            self.assertIs(stop_iperf.call_args.args[1], iperf)
            self.assertFalse(manager.services_running)
            self.assertEqual(manager.services, [])

    def test_cleanup_continues_after_one_failure(self):
        manager = TrafficManager(Mock())
        manager._http_services['web_srv'] = Mock()
        manager._iperf_services['db_srv'] = Mock()
        with patch('src.traffic.manager.stop_http_server', side_effect=RuntimeError('stop failed')), \
             patch('src.traffic.manager.stop_iperf_server') as stop:
            with self.assertRaisesRegex(RuntimeError, 'stop failed'):
                manager.teardown_services()
            stop.assert_called_once()
            self.assertEqual(len(manager.services), 1)

    def test_http_200_with_transfer_failure_is_not_success(self):
        with patch('src.traffic.http.run_probe', return_value=(
                'status=200|time_total=1.0|time_connect=0.01', 28, 'timeout')):
            measurement = measure_http(Mock(), 'http://example.invalid')
        self.assertFalse(measurement.success)
        self.assertEqual(measurement.exit_code, 28)

    def test_invalid_iperf_schema_and_nonfinite_values_fail(self):
        for raw in ['[]', 'null', '{"end": null}',
                    '{"end":{"sum_received":{"bits_per_second":NaN,"seconds":1}}}']:
            result = parse_iperf_json(raw)
            self.assertFalse(result['success'])
            self.assertIsNone(result['throughput_mbps'])
            json.dumps(result, allow_nan=False)

    def test_missing_ping_and_summary_are_unavailable(self):
        result = parse_ping_output('ping: command not found')
        self.assertFalse(result['success'])
        self.assertIsNone(result['packet_loss_percent'])
        self.assertIsNone(result['rtt_avg_ms'])
        summary = TrafficManager.summarize([], [], [])
        self.assertFalse(summary['success'])
        self.assertIsNone(summary['summary']['ping_latency_ms'])
        self.assertIsNone(summary['summary']['throughput_mbps'])
        self.assertIsNone(summary['summary']['http_response_time_ms'])


class TestExperimentReports(unittest.TestCase):
    def run_mock_experiment(self, outcome, teardown_error=None):
        events = []
        @contextmanager
        def network(state):
            try:
                yield Mock(hosts=[None] * 7, switches=[None] * 5)
            finally:
                events.append('network stopped')
                state['cleanup_success'] = True
        manager = Mock(services=[])
        manager.collect_all_kpis.side_effect = outcome if isinstance(outcome, BaseException) else None
        manager.collect_all_kpis.return_value = outcome
        manager.teardown_services.side_effect = teardown_error
        with tempfile.TemporaryDirectory() as directory, \
             patch('scripts.run_traffic_test.experiment_network', network), \
             patch('scripts.run_traffic_test.TrafficManager', return_value=manager), \
             patch('sys.stdout', new_callable=io.StringIO) as output, \
             patch('sys.stderr', new_callable=io.StringIO):
            status = run_traffic_experiment(directory)
            files = list(Path(directory).glob('*.json'))
            self.assertEqual(len(files), 1)
            report = json.loads(files[0].read_text())
            terminal = output.getvalue()
        manager.teardown_services.assert_called_once()
        self.assertEqual(events, ['network stopped'])
        return status, report, terminal, manager

    def test_same_cycle_is_printed_and_saved(self):
        kpis = dict(success=True, summary={'ping_latency_ms': 12.345},
                    icmp=[], http=[], throughput=[])
        status, report, output, manager = self.run_mock_experiment(kpis)
        self.assertEqual(status, 0)
        self.assertEqual(report['summary'], kpis['summary'])
        self.assertIn('"ping_latency_ms": 12.345', output)
        manager.collect_all_kpis.assert_called_once()
        self.assertTrue(report['cleanup_success'])

    def test_probe_failures_return_nonzero_and_save_evidence(self):
        kpis = dict(success=False, summary={'ping_latency_ms': None},
                    icmp=[], http=[], throughput=[])
        status, report, _, _ = self.run_mock_experiment(kpis)
        self.assertEqual(status, 1)
        self.assertFalse(report['success'])
        self.assertTrue(report['errors'])

    def test_interrupt_and_startup_failure_save_failure_reports(self):
        for error, expected in [(KeyboardInterrupt(), 130), (RuntimeError('startup failed'), 1)]:
            status, report, _, _ = self.run_mock_experiment(error)
            self.assertEqual(status, expected)
            self.assertFalse(report['success'])
            self.assertTrue(report['cleanup_success'])

    def test_teardown_failure_still_stops_network(self):
        kpis = dict(success=True, summary={}, icmp=[], http=[], throughput=[])
        status, report, _, _ = self.run_mock_experiment(kpis, RuntimeError('cleanup failed'))
        self.assertEqual(status, 1)
        self.assertFalse(report['cleanup_success'])

    def test_reports_do_not_overwrite_and_reject_nan(self):
        with tempfile.TemporaryDirectory() as directory:
            first = save_results({'value': 1}, directory)
            second = save_results({'value': 2}, directory)
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads(first.read_text())['value'], 1)
            with self.assertRaises(ValueError):
                save_results({'value': float('nan')}, directory)


if __name__ == '__main__':
    unittest.main(verbosity=2)
