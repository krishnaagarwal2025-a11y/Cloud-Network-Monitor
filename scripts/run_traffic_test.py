#!/usr/bin/env python3
"""
Traffic Generation & KPI Measurement Runner
-------------------------------------------
Starts the Mininet cloud topology, launches application services (HTTP, iperf3),
runs comprehensive measurements (ICMP, HTTP, throughput), prints formatted KPI
summaries, and saves structured JSON metrics for telemetry ingestion.

Usage:
    sudo python3 scripts/run_traffic_test.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.topology.cloud_topology import create_network
from src.traffic.manager import TrafficManager


def format_table(headers, rows):
    """Simple formatted table printer."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))
            
    header_line = " | ".join(f"{h:<{w}}" for h, w in zip(headers, col_widths))
    sep_line = "-+-".join("-" * w for w in col_widths)
    output = [header_line, sep_line]
    for row in rows:
        output.append(" | ".join(f"{str(v):<{w}}" for v, w in zip(row, col_widths)))
    return "\n".join(output)


def run_traffic_experiment():
    """Execute complete live traffic generation and KPI measurement run."""
    print("=" * 70)
    print(" Cloud Application Network Monitoring - Phase 3 Traffic & KPIs")
    print("=" * 70)

    net = None
    manager = None

    try:
        print("\n[1/5] Instantiating and starting Mininet cloud topology...")
        net = create_network()
        net.start()
        print("  ✓ Topology started successfully (7 hosts, 5 OVSBridge switches)")

        manager = TrafficManager(net)

        print("\n[2/5] Setting up background application services...")
        manager.setup_services(http_port=8080, iperf_port=5201)
        print("  ✓ HTTP server running on web_srv:8080")
        print("  ✓ iperf3 servers running on web_srv:5201 and db_srv:5201")

        print("\n[3/5] Measuring ICMP Ping Latency & Packet Loss...")
        icmp_paths = [
            ('client', 'edge_gw'),
            ('client', 'web_srv'),
            ('client', 'api_srv'),
            ('client', 'db_srv'),
            ('web_srv', 'db_srv'),
        ]
        icmp_results = manager.run_icmp_suite(paths=icmp_paths, count=3)

        icmp_rows = []
        for r in icmp_results:
            icmp_rows.append([
                f"{r.source} -> {r.target}",
                r.target_ip,
                f"{r.packets_received}/{r.packets_sent}",
                f"{r.packet_loss_percent:.1f}%",
                f"{r.rtt_avg_ms:.2f} ms",
                f"{r.rtt_min_ms:.2f} ms",
                f"{r.rtt_max_ms:.2f} ms",
                f"{r.rtt_mdev_ms:.2f} ms",
                "PASS" if r.success else "FAIL",
            ])
        print(format_table(
            ["Path", "Target IP", "Packets", "Loss", "Avg RTT", "Min RTT", "Max RTT", "Jitter", "Status"],
            icmp_rows
        ))

        print("\n[4/5] Measuring HTTP Response Time (client -> web_srv:8080)...")
        http_results = manager.run_http_suite(count=5, delay=0.2)
        http_rows = []
        for idx, h in enumerate(http_results, 1):
            http_rows.append([
                f"Req #{idx}",
                f"{h.source} -> {h.target}",
                h.url,
                h.http_status,
                f"{h.connect_time_ms:.2f} ms",
                f"{h.response_time_ms:.2f} ms",
                "200 OK" if h.success else "ERROR",
            ])
        print(format_table(
            ["Request", "Source -> Target", "URL", "Status Code", "Connect Time", "Total Time", "Result"],
            http_rows
        ))

        print("\n[5/5] Measuring TCP Throughput with iperf3...")
        iperf_results = manager.run_iperf_suite(targets=['web_srv', 'db_srv'], duration=3)
        iperf_rows = []
        for i in iperf_results:
            iperf_rows.append([
                f"{i.source} -> {i.target}",
                i.target_ip,
                f"{i.duration_sec:.1f} s",
                f"{i.throughput_mbps:.2f} Mbps",
                i.retransmits,
                f"{i.mean_rtt_ms:.2f} ms",
                "PASS" if i.success else "FAIL",
            ])
        print(format_table(
            ["Path", "Target IP", "Duration", "Throughput", "Retransmits", "Mean RTT", "Status"],
            iperf_rows
        ))

        # Compile structured telemetry JSON
        kpis = manager.collect_all_kpis(icmp_count=2, http_count=2, iperf_duration=2)

        print("\n" + "=" * 70)
        print(" Summary KPI Metrics (Ready for Prometheus Telemetry Exporter)")
        print("=" * 70)
        print(f"  • ping_latency_ms:        {kpis['summary']['ping_latency_ms']:.2f} ms")
        print(f"  • packet_loss_percent:    {kpis['summary']['packet_loss_percent']:.1f} %")
        print(f"  • http_response_time_ms:  {kpis['summary']['http_response_time_ms']:.2f} ms")
        print(f"  • http_success:           {kpis['summary']['http_success']}")
        print(f"  • throughput_mbps:        {kpis['summary']['throughput_mbps']:.2f} Mbps")
        print("=" * 70)

        # Save to results directory
        os.makedirs("results", exist_ok=True)
        results_file = "results/traffic_metrics_latest.json"
        with open(results_file, "w") as f:
            json.dump(kpis, f, indent=2)
        print(f"\n✓ Saved structured metrics to: {results_file}")

    finally:
        if manager:
            print("\nTearing down background services...")
            manager.teardown_services()
        if net:
            print("Stopping Mininet network...")
            net.stop()
        print("Done. Cleaned up successfully.\n")


if __name__ == '__main__':
    if os.geteuid() != 0:
        print("\n[ERROR] Mininet requires root (sudo) privileges.")
        print("Please run with:")
        print("    sudo python3 scripts/run_traffic_test.py\n")
        sys.exit(1)
    run_traffic_experiment()
