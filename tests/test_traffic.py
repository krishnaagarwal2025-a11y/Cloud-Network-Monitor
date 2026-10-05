#!/usr/bin/env python3
"""
Unit and Integration Tests for Traffic Generation & Measurement
--------------------------------------------------------------
Tests:
1. ICMP ping output parsing (normal, 100% loss, edge cases).
2. HTTP curl output parsing (200 OK, 500 Error, connection refused).
3. iperf3 JSON output parsing (valid bandwidth, error responses).
4. Live end-to-end traffic measurement on Mininet (when run with root).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.traffic.icmp import parse_ping_output
from src.traffic.http import parse_curl_output
from src.traffic.iperf import parse_iperf_json
from src.topology.cloud_topology import create_network
from src.traffic.manager import TrafficManager


class TestICMPParsing(unittest.TestCase):
    """Unit tests for ICMP ping parser."""

    def test_parse_normal_ping_output(self):
        sample = """
PING 10.0.0.11 (10.0.0.11) 56(84) bytes of data.
64 bytes from 10.0.0.11: icmp_seq=1 ttl=64 time=8.12 ms
64 bytes from 10.0.0.11: icmp_seq=2 ttl=64 time=8.34 ms
64 bytes from 10.0.0.11: icmp_seq=3 ttl=64 time=8.05 ms

--- 10.0.0.11 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2003ms
rtt min/avg/max/mdev = 8.050/8.170/8.340/0.124 ms
"""
        parsed = parse_ping_output(sample)
        self.assertEqual(parsed['packets_sent'], 3)
        self.assertEqual(parsed['packets_received'], 3)
        self.assertEqual(parsed['packet_loss_percent'], 0.0)
        self.assertEqual(parsed['rtt_min_ms'], 8.05)
        self.assertEqual(parsed['rtt_avg_ms'], 8.17)
        self.assertEqual(parsed['rtt_max_ms'], 8.34)
        self.assertEqual(parsed['rtt_mdev_ms'], 0.124)
        self.assertTrue(parsed['success'])

    def test_parse_100_percent_loss(self):
        sample = """
PING 10.0.0.99 (10.0.0.99) 56(84) bytes of data.

--- 10.0.0.99 ping statistics ---
3 packets transmitted, 0 received, 100% packet loss, time 2045ms
"""
        parsed = parse_ping_output(sample)
        self.assertEqual(parsed['packets_sent'], 3)
        self.assertEqual(parsed['packets_received'], 0)
        self.assertEqual(parsed['packet_loss_percent'], 100.0)
        self.assertEqual(parsed['rtt_avg_ms'], 0.0)
        self.assertFalse(parsed['success'])


class TestHTTPParsing(unittest.TestCase):
    """Unit tests for HTTP curl parser."""

    def test_parse_successful_http(self):
        sample = "status=200|time_total=0.015234|time_connect=0.003120"
        parsed = parse_curl_output(sample)
        self.assertEqual(parsed['http_status'], 200)
        self.assertEqual(parsed['response_time_ms'], 15.234)
        self.assertEqual(parsed['connect_time_ms'], 3.120)
        self.assertTrue(parsed['success'])

    def test_parse_failed_http_status(self):
        sample = "status=500|time_total=0.025000|time_connect=0.005000"
        parsed = parse_curl_output(sample)
        self.assertEqual(parsed['http_status'], 500)
        self.assertFalse(parsed['success'])

    def test_parse_connection_refused(self):
        sample = "status=000|time_total=0.000100|time_connect=0.000000"
        parsed = parse_curl_output(sample)
        self.assertEqual(parsed['http_status'], 0)
        self.assertFalse(parsed['success'])


class TestIperfParsing(unittest.TestCase):
    """Unit tests for iperf3 JSON parser."""

    def test_parse_valid_iperf_json(self):
        sample = """
{
  "start": { "connected": [] },
  "end": {
    "sum_sent": { "seconds": 2.0001, "bytes": 245000000, "bits_per_second": 980000000.0, "retransmits": 2 },
    "sum_received": { "seconds": 2.0001, "bytes": 245000000, "bits_per_second": 978500000.0 },
    "streams": [{
      "sender": { "mean_rtt": 8150 }
    }]
  }
}
"""
        parsed = parse_iperf_json(sample)
        self.assertEqual(parsed['throughput_mbps'], 978.5)
        self.assertEqual(parsed['retransmits'], 2)
        self.assertEqual(parsed['mean_rtt_ms'], 8.15)
        self.assertTrue(parsed['success'])

    def test_parse_error_iperf_json(self):
        sample = '{"error": "unable to connect to server: Connection refused"}'
        parsed = parse_iperf_json(sample)
        self.assertEqual(parsed['throughput_mbps'], 0.0)
        self.assertFalse(parsed['success'])
        self.assertIn("Connection refused", parsed['error_message'])

    def test_parse_malformed_json(self):
        parsed = parse_iperf_json("invalid json string")
        self.assertEqual(parsed['throughput_mbps'], 0.0)
        self.assertFalse(parsed['success'])


class TestTrafficLiveIntegration(unittest.TestCase):
    """
    Live traffic integration test running inside Mininet.
    Requires root (sudo) privileges.
    """

    @unittest.skipUnless(os.geteuid() == 0, "Live Mininet test requires root (sudo) privileges.")
    def test_live_traffic_suite(self):
        net = None
        manager = None
        try:
            print("\n*** Starting Mininet for live traffic measurement test...")
            net = create_network()
            net.start()

            manager = TrafficManager(net)
            print("*** Setting up background HTTP and iperf3 services...")
            manager.setup_services(http_port=8080, iperf_port=5201)

            print("*** Running live ICMP measurements...")
            icmp_results = manager.run_icmp_suite(
                paths=[('client', 'web_srv'), ('client', 'db_srv')],
                count=2,
            )
            self.assertEqual(len(icmp_results), 2)
            for res in icmp_results:
                self.assertTrue(res.success, f"ICMP ping failed: {res.source} -> {res.target}")
                self.assertGreater(res.rtt_avg_ms, 0.0)
                self.assertEqual(res.packet_loss_percent, 0.0)

            print("*** Running live HTTP measurement...")
            http_results = manager.run_http_suite(count=2, delay=0.1)
            self.assertEqual(len(http_results), 2)
            for res in http_results:
                self.assertTrue(res.success, f"HTTP curl probe failed: {res.url}")
                self.assertEqual(res.http_status, 200)
                self.assertGreater(res.response_time_ms, 0.0)

            print("*** Running live iperf3 throughput measurement...")
            iperf_results = manager.run_iperf_suite(targets=['web_srv'], duration=1)
            self.assertEqual(len(iperf_results), 1)
            self.assertTrue(iperf_results[0].success, f"iperf3 failed: {iperf_results[0].error_message}")
            self.assertGreater(iperf_results[0].throughput_mbps, 0.0)
            print(f"*** Measured throughput: {iperf_results[0].throughput_mbps} Mbps")

            print("*** Collecting aggregated KPI dictionary...")
            kpis = manager.collect_all_kpis(icmp_count=2, http_count=2, iperf_duration=1)
            self.assertIn('summary', kpis)
            self.assertIn('ping_latency_ms', kpis['summary'])
            self.assertIn('http_response_time_ms', kpis['summary'])
            self.assertIn('throughput_mbps', kpis['summary'])

        finally:
            if manager:
                print("*** Tearing down background traffic services...")
                manager.teardown_services()
            if net:
                print("*** Stopping Mininet network...")
                net.stop()


def run_tests():
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
