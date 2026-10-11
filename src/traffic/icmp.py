#!/usr/bin/env python3
"""
ICMP Ping Monitor
-----------------
Measures round-trip time (min, avg, max, mdev/jitter) and packet loss
between Mininet hosts.
"""

from dataclasses import dataclass, asdict
import re
import math
import time
from src.traffic.process import run_probe
from typing import Optional, Dict, Any


@dataclass
class ICMPMeasurement:
    source: str
    target: str
    target_ip: str
    packets_sent: int
    packets_received: int
    packet_loss_percent: Optional[float]
    rtt_min_ms: Optional[float]
    rtt_avg_ms: Optional[float]
    rtt_max_ms: Optional[float]
    rtt_mdev_ms: Optional[float]
    success: bool
    raw_output: str
    exit_code: Optional[int] = None
    error_message: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_ping_output(raw_output: str) -> Dict[str, Any]:
    """
    Parse standard iputils ping output into structured metrics.
    """
    packets_sent = 0
    packets_received = 0
    packet_loss_percent = None
    rtt_min = None
    rtt_avg = None
    rtt_max = None
    rtt_mdev = None

    # Match transmitted / received packets and loss percentage
    # e.g.: "3 packets transmitted, 3 received, 0% packet loss, time 2004ms"
    pkt_match = re.search(
        r'(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received.*?(\d+(?:\.\d+)?)%\s+packet loss',
        raw_output,
        re.IGNORECASE,
    )
    if pkt_match:
        packets_sent = int(pkt_match.group(1))
        packets_received = int(pkt_match.group(2))
        packet_loss_percent = float(pkt_match.group(3))

    # Match RTT statistics
    # e.g.: "rtt min/avg/max/mdev = 0.038/0.041/0.045/0.003 ms"
    rtt_match = re.search(
        r'(?:rtt|round-trip)\s+min/avg/max/(?:mdev|stddev)\s*=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+)\s*ms',
        raw_output,
        re.IGNORECASE,
    )
    if rtt_match:
        try:
            values = [float(rtt_match.group(i)) for i in range(1, 5)]
            if all(math.isfinite(v) and v >= 0 for v in values):
                rtt_min, rtt_avg, rtt_max, rtt_mdev = values
        except ValueError:
            pass

    success = packets_received > 0 and rtt_avg is not None

    return {
        'packets_sent': packets_sent,
        'packets_received': packets_received,
        'packet_loss_percent': packet_loss_percent,
        'rtt_min_ms': rtt_min,
        'rtt_avg_ms': rtt_avg,
        'rtt_max_ms': rtt_max,
        'rtt_mdev_ms': rtt_mdev,
        'success': success,
    }


def measure_ping(
    source_node,
    target_ip: str,
    target_name: str = "",
    count: int = 3,
    timeout: int = 2,
    interval: float = 0.2,
) -> ICMPMeasurement:
    """
    Execute ping command from a Mininet host and return an ICMPMeasurement.
    """
    if count <= 0 or timeout <= 0 or interval <= 0:
        raise ValueError('Ping count, timeout and interval must be positive')
    src_name = getattr(source_node, 'name', str(source_node))
    tgt_name = target_name or target_ip

    argv = ['ping', '-n', '-c', str(count), '-W', str(timeout), '-i', str(interval), target_ip]
    raw_output, exit_code, error = run_probe(source_node, argv, count * (timeout + interval) + 2)

    parsed = parse_ping_output(raw_output)
    parsed['success'] = parsed['success'] and exit_code == 0 and not error
    if not parsed['success'] and not error:
        error = 'No valid ping replies/statistics'

    return ICMPMeasurement(
        source=src_name,
        target=tgt_name,
        target_ip=target_ip,
        packets_sent=parsed['packets_sent'],
        packets_received=parsed['packets_received'],
        packet_loss_percent=parsed['packet_loss_percent'],
        rtt_min_ms=parsed['rtt_min_ms'],
        rtt_avg_ms=parsed['rtt_avg_ms'],
        rtt_max_ms=parsed['rtt_max_ms'],
        rtt_mdev_ms=parsed['rtt_mdev_ms'],
        success=parsed['success'],
        raw_output=raw_output.strip(),
        exit_code=exit_code,
        error_message=error,
    )
