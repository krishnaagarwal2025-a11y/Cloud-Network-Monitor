#!/usr/bin/env python3
"""
Traffic Manager & KPI Collection Orchestrator
---------------------------------------------
Coordinates ICMP ping, HTTP curl probing, and iperf3 throughput tests
across the Mininet cloud topology. Produces structured KPI measurements
ready for telemetry storage and Prometheus exporter consumption.
"""

import time
from typing import Dict, List, Any, Optional
from src.traffic.icmp import ICMPMeasurement, measure_ping
from src.traffic.http import HTTPMeasurement, measure_http, start_http_server, stop_http_server
from src.traffic.iperf import IperfMeasurement, measure_throughput, start_iperf_server, stop_iperf_server


class TrafficManager:
    """
    Manages background services and conducts end-to-end KPI measurements.
    """

    def __init__(self, net):
        self.net = net
        self.services_running = False
        self.http_port = 8080
        self.iperf_port = 5201
        self._http_pids: Dict[str, int] = {}
        self._iperf_pids: Dict[str, int] = {}

    def setup_services(self, http_port: int = 8080, iperf_port: int = 5201):
        """
        Start application servers on target hosts:
        - HTTP server on web_srv
        - iperf3 servers on web_srv and db_srv
        """
        self.http_port = http_port
        self.iperf_port = iperf_port

        # 1. Start HTTP Server on web_srv
        web_srv = self.net.get('web_srv')
        if web_srv:
            pid = start_http_server(web_srv, port=self.http_port)
            if pid:
                self._http_pids['web_srv'] = pid

        # 2. Start iperf3 server on web_srv and db_srv
        for host_name in ['web_srv', 'db_srv']:
            host = self.net.get(host_name)
            if host:
                pid = start_iperf_server(host, port=self.iperf_port)
                if pid:
                    self._iperf_pids[host_name] = pid

        self.services_running = True
        time.sleep(0.5)

    def teardown_services(self):
        """
        Gracefully stop all background HTTP and iperf3 services.
        """
        for host_name, pid in self._http_pids.items():
            host = self.net.get(host_name)
            if host:
                stop_http_server(host, pid=pid, port=self.http_port)
        self._http_pids.clear()

        for host_name, pid in self._iperf_pids.items():
            host = self.net.get(host_name)
            if host:
                stop_iperf_server(host, pid=pid, port=self.iperf_port)
        self._iperf_pids.clear()

        self.services_running = False

    def run_icmp_suite(
        self,
        paths: Optional[List[tuple]] = None,
        count: int = 3,
    ) -> List[ICMPMeasurement]:
        """
        Run ICMP latency and loss measurements across designated paths.
        Default paths:
          - client -> edge_gw
          - client -> web_srv
          - client -> api_srv
          - client -> db_srv
          - web_srv -> db_srv
        """
        if paths is None:
            paths = [
                ('client', 'edge_gw'),
                ('client', 'web_srv'),
                ('client', 'api_srv'),
                ('client', 'db_srv'),
                ('web_srv', 'db_srv'),
            ]

        results = []
        for src_name, tgt_name in paths:
            src = self.net.get(src_name)
            tgt = self.net.get(tgt_name)
            if src and tgt:
                res = measure_ping(
                    source_node=src,
                    target_ip=tgt.IP(),
                    target_name=tgt_name,
                    count=count,
                )
                results.append(res)
        return results

    def run_http_suite(
        self,
        count: int = 3,
        delay: float = 0.2,
    ) -> List[HTTPMeasurement]:
        """
        Run repeated HTTP requests from client to web_srv.
        """
        client = self.net.get('client')
        web_srv = self.net.get('web_srv')
        if not client or not web_srv:
            return []

        url = f"http://{web_srv.IP()}:{self.http_port}/"
        results = []

        for _ in range(count):
            res = measure_http(
                source_node=client,
                url=url,
                target_name='web_srv',
            )
            results.append(res)
            if delay > 0:
                time.sleep(delay)

        return results

    def run_iperf_suite(
        self,
        targets: Optional[List[str]] = None,
        duration: int = 2,
    ) -> List[IperfMeasurement]:
        """
        Run iperf3 throughput tests from client to target hosts.
        Default targets: ['web_srv', 'db_srv']
        """
        client = self.net.get('client')
        if not client:
            return []

        if targets is None:
            targets = ['web_srv', 'db_srv']

        results = []
        for tgt_name in targets:
            tgt = self.net.get(tgt_name)
            if tgt:
                res = measure_throughput(
                    source_node=client,
                    target_ip=tgt.IP(),
                    target_name=tgt_name,
                    port=self.iperf_port,
                    duration=duration,
                )
                results.append(res)

        return results

    def collect_all_kpis(
        self,
        icmp_count: int = 3,
        http_count: int = 3,
        iperf_duration: int = 2,
    ) -> Dict[str, Any]:
        """
        Execute full measurement cycle and return aggregated structured metrics.
        """
        if not self.services_running:
            self.setup_services()

        icmp_results = self.run_icmp_suite(count=icmp_count)
        http_results = self.run_http_suite(count=http_count)
        iperf_results = self.run_iperf_suite(duration=iperf_duration)

        # Compute summary metrics across client -> web_srv primary path
        primary_icmp = next(
            (r for r in icmp_results if r.source == 'client' and r.target == 'web_srv'),
            None,
        )
        avg_http_ms = (
            round(sum(h.response_time_ms for h in http_results) / len(http_results), 2)
            if http_results else 0.0
        )
        http_success_rate = (
            round(sum(1 for h in http_results if h.success) / len(http_results) * 100.0, 1)
            if http_results else 0.0
        )
        primary_iperf = next(
            (i for i in iperf_results if i.target == 'web_srv'),
            None,
        )

        return {
            'timestamp': time.time(),
            'summary': {
                'ping_latency_ms': primary_icmp.rtt_avg_ms if primary_icmp else 0.0,
                'packet_loss_percent': primary_icmp.packet_loss_percent if primary_icmp else 0.0,
                'http_response_time_ms': avg_http_ms,
                'http_success': 1 if http_success_rate == 100.0 else 0,
                'throughput_mbps': primary_iperf.throughput_mbps if primary_iperf else 0.0,
            },
            'icmp': [r.to_dict() for r in icmp_results],
            'http': [h.to_dict() for h in http_results],
            'throughput': [i.to_dict() for i in iperf_results],
        }
