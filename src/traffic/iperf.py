#!/usr/bin/env python3
"""
iperf3 Throughput Monitor
-------------------------
Starts iperf3 servers on target hosts and runs client throughput
measurements using iperf3 JSON output (-J).
"""

from dataclasses import dataclass, asdict
import json
import time
from typing import Optional, Dict, Any


@dataclass
class IperfMeasurement:
    source: str
    target: str
    target_ip: str
    port: int
    throughput_mbps: float
    retransmits: int
    duration_sec: float
    mean_rtt_ms: float
    success: bool
    error_message: str
    raw_output: str
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_iperf_json(raw_output: str) -> Dict[str, Any]:
    """
    Parse iperf3 -J JSON output into structured metrics.
    """
    throughput_mbps = 0.0
    retransmits = 0
    duration_sec = 0.0
    mean_rtt_ms = 0.0
    success = False
    error_msg = ""

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        return {
            'throughput_mbps': 0.0,
            'retransmits': 0,
            'duration_sec': 0.0,
            'mean_rtt_ms': 0.0,
            'success': False,
            'error_message': f"JSON decode error: {exc}",
        }

    # Check for top-level error reported by iperf3
    if 'error' in data:
        return {
            'throughput_mbps': 0.0,
            'retransmits': 0,
            'duration_sec': 0.0,
            'mean_rtt_ms': 0.0,
            'success': False,
            'error_message': str(data.get('error')),
        }

    end_data = data.get('end', {})
    
    # Received throughput
    sum_received = end_data.get('sum_received', {})
    sum_sent = end_data.get('sum_sent', {})

    if 'bits_per_second' in sum_received:
        bps = sum_received['bits_per_second']
        throughput_mbps = round(float(bps) / 1e6, 2)
        duration_sec = round(float(sum_received.get('seconds', 0.0)), 2)
    elif 'bits_per_second' in sum_sent:
        bps = sum_sent['bits_per_second']
        throughput_mbps = round(float(bps) / 1e6, 2)
        duration_sec = round(float(sum_sent.get('seconds', 0.0)), 2)

    # Retransmissions
    retransmits = int(sum_sent.get('retransmits', 0))

    # Mean RTT from streams if available
    streams = end_data.get('streams', [])
    if streams and isinstance(streams, list):
        sender_stream = streams[0].get('sender', {})
        if 'mean_rtt' in sender_stream:
            # iperf3 returns mean_rtt in microseconds
            mean_rtt_ms = round(float(sender_stream['mean_rtt']) / 1000.0, 3)

    success = (throughput_mbps > 0.0)

    return {
        'throughput_mbps': throughput_mbps,
        'retransmits': retransmits,
        'duration_sec': duration_sec,
        'mean_rtt_ms': mean_rtt_ms,
        'success': success,
        'error_message': error_msg,
    }


def start_iperf_server(host_node, port: int = 5201) -> Optional[int]:
    """
    Start an iperf3 server daemon on the given Mininet host node.
    Returns the server PID if known.
    """
    stop_iperf_server(host_node, port=port)

    # Run iperf3 in server mode as daemon or background process
    cmd = f"iperf3 -s -p {port} > /tmp/iperf3_srv_{port}.log 2>&1 & echo $!"
    pid_str = host_node.cmd(cmd).strip().split()[-1]
    time.sleep(0.5)

    try:
        return int(pid_str)
    except (ValueError, IndexError):
        return None


def stop_iperf_server(host_node, pid: Optional[int] = None, port: int = 5201):
    """
    Stop the iperf3 server on the given Mininet host node.
    """
    if pid:
        host_node.cmd(f"kill -9 {pid} 2>/dev/null || true")
    host_node.cmd(f"pkill -9 -f 'iperf3 -s -p {port}' 2>/dev/null || true")
    host_node.cmd(f"fuser -k {port}/tcp 2>/dev/null || true")


def measure_throughput(
    source_node,
    target_ip: str,
    target_name: str = "",
    port: int = 5201,
    duration: int = 3,
) -> IperfMeasurement:
    """
    Run an iperf3 throughput test from source_node to target_ip and return an IperfMeasurement.
    """
    src_name = getattr(source_node, 'name', str(source_node))
    tgt_name = target_name or target_ip

    cmd = f"iperf3 -c {target_ip} -p {port} -t {duration} -J"
    raw_output = source_node.cmd(cmd)

    parsed = parse_iperf_json(raw_output)

    return IperfMeasurement(
        source=src_name,
        target=tgt_name,
        target_ip=target_ip,
        port=port,
        throughput_mbps=parsed['throughput_mbps'],
        retransmits=parsed['retransmits'],
        duration_sec=parsed['duration_sec'],
        mean_rtt_ms=parsed['mean_rtt_ms'],
        success=parsed['success'],
        error_message=parsed['error_message'],
        raw_output=raw_output.strip(),
    )
