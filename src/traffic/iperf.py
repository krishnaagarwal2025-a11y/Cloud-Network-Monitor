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
import math
from src.traffic.process import start_service, run_probe
from typing import Optional, Dict, Any


@dataclass
class IperfMeasurement:
    source: str
    target: str
    target_ip: str
    port: int
    throughput_mbps: Optional[float]
    retransmits: Optional[int]
    duration_sec: Optional[float]
    mean_rtt_ms: Optional[float]
    success: bool
    error_message: str
    raw_output: str
    exit_code: Optional[int] = None
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _parse_iperf_json(raw_output: str) -> Dict[str, Any]:
    """
    Parse iperf3 -J JSON output into structured metrics.
    """
    throughput_mbps = 0.0
    retransmits = None
    duration_sec = 0.0
    mean_rtt_ms = None
    success = False
    error_msg = ""

    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        return {
            'throughput_mbps': 0.0,
            'retransmits': None,
            'duration_sec': 0.0,
            'mean_rtt_ms': None,
            'success': False,
            'error_message': f"JSON decode error: {exc}",
        }

    # Check for top-level error reported by iperf3
    if 'error' in data:
        return {
            'throughput_mbps': 0.0,
            'retransmits': None,
            'duration_sec': 0.0,
            'mean_rtt_ms': None,
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
    retransmits = int(sum_sent['retransmits']) if 'retransmits' in sum_sent else None

    # Mean RTT from streams if available
    streams = end_data.get('streams', [])
    if streams and isinstance(streams, list):
        sender_stream = streams[0].get('sender', {})
        if 'mean_rtt' in sender_stream:
            # iperf3 returns mean_rtt in microseconds
            mean_rtt_ms = round(float(sender_stream['mean_rtt']) / 1000.0, 3)

    success = (throughput_mbps > 0.0 and duration_sec > 0.0)

    return {
        'throughput_mbps': throughput_mbps,
        'retransmits': retransmits,
        'duration_sec': duration_sec,
        'mean_rtt_ms': mean_rtt_ms,
        'success': success,
        'error_message': error_msg,
    }


def parse_iperf_json(raw_output: str) -> Dict[str, Any]:
    """Reject invalid schemas and non-finite values as explicit failures."""
    try:
        parsed = _parse_iperf_json(raw_output)
        if any(isinstance(v, float) and not math.isfinite(v) for v in parsed.values()):
            raise ValueError('Non-finite iperf metric')
        if not parsed['success']:
            parsed['throughput_mbps'] = None
            parsed['duration_sec'] = None
            parsed['error_message'] = parsed['error_message'] or 'Missing positive throughput/duration'
        return parsed
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, OverflowError) as exc:
        return dict(throughput_mbps=None, retransmits=None, duration_sec=None,
                    mean_rtt_ms=None, success=False, error_message=f'Invalid iperf data: {exc}')


def start_iperf_server(host_node, port: int = 5201):
    return start_service(host_node, ['iperf3', '-s', '-B', host_node.IP(),
                                    '-p', str(port)], port)


def stop_iperf_server(host_node, service):
    service.stop()


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
    if duration <= 0:
        raise ValueError('iperf duration must be positive')
    src_name = getattr(source_node, 'name', str(source_node))
    tgt_name = target_name or target_ip

    argv = ['iperf3', '-c', target_ip, '-p', str(port), '-t', str(duration),
            '--connect-timeout', '3000', '-J']
    raw_output, exit_code, error = run_probe(source_node, argv, duration + 10)

    parsed = parse_iperf_json(raw_output)
    if error or exit_code != 0:
        parsed['success'] = False
        parsed['throughput_mbps'] = None
        parsed['error_message'] = error or parsed['error_message']

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
        exit_code=exit_code,
    )
