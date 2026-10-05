#!/usr/bin/env python3
"""
HTTP Service & Response Time Monitor
------------------------------------
Starts a lightweight HTTP service on target hosts (e.g. web_srv)
and measures response time, connect time, and HTTP status codes using curl.
"""

from dataclasses import dataclass, asdict
import re
import time
from typing import Optional, Dict, Any


@dataclass
class HTTPMeasurement:
    source: str
    target: str
    url: str
    http_status: int
    response_time_ms: float
    connect_time_ms: float
    success: bool
    raw_output: str
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_curl_output(raw_output: str) -> Dict[str, Any]:
    """
    Parse curl formatted output:
    status=200|time_total=0.012345|time_connect=0.004567
    """
    http_status = 0
    response_time_ms = 0.0
    connect_time_ms = 0.0

    match_status = re.search(r'status=(\d+)', raw_output)
    if match_status:
        try:
            http_status = int(match_status.group(1))
        except ValueError:
            http_status = 0

    match_total = re.search(r'time_total=([0-9.]+)', raw_output)
    if match_total:
        try:
            # Convert seconds to milliseconds
            response_time_ms = round(float(match_total.group(1)) * 1000.0, 3)
        except ValueError:
            response_time_ms = 0.0

    match_connect = re.search(r'time_connect=([0-9.]+)', raw_output)
    if match_connect:
        try:
            connect_time_ms = round(float(match_connect.group(1)) * 1000.0, 3)
        except ValueError:
            connect_time_ms = 0.0

    success = (200 <= http_status < 400)

    return {
        'http_status': http_status,
        'response_time_ms': response_time_ms,
        'connect_time_ms': connect_time_ms,
        'success': success,
    }


def start_http_server(host_node, port: int = 8080) -> Optional[int]:
    """
    Start a lightweight HTTP server on the given Mininet host node in the background.
    Returns the process PID.
    """
    # Kill any existing server on this port
    stop_http_server(host_node, port=port)

    # Start built-in http.server
    cmd = (
        f"python3 -m http.server {port} --bind 0.0.0.0 > /tmp/http_server_{port}.log 2>&1 & echo $!"
    )
    pid_str = host_node.cmd(cmd).strip().split()[-1]
    time.sleep(0.5)  # Allow process to bind socket

    try:
        return int(pid_str)
    except (ValueError, IndexError):
        return None


def stop_http_server(host_node, pid: Optional[int] = None, port: int = 8080):
    """
    Stop the HTTP server on the given Mininet host node.
    """
    if pid:
        host_node.cmd(f"kill -9 {pid} 2>/dev/null || true")
    # Kill any process listening on the port
    host_node.cmd(f"pkill -9 -f 'http.server {port}' 2>/dev/null || true")
    host_node.cmd(f"fuser -k {port}/tcp 2>/dev/null || true")


def measure_http(
    source_node,
    url: str,
    target_name: str = "",
    timeout: int = 3,
) -> HTTPMeasurement:
    """
    Probe the HTTP URL from source_node using curl and return an HTTPMeasurement.
    """
    src_name = getattr(source_node, 'name', str(source_node))
    tgt_name = target_name or url

    # Format output with status, total time, and connect time
    format_str = "status=%{http_code}|time_total=%{time_total}|time_connect=%{time_connect}"
    cmd = (
        f'curl -s -o /dev/null -w "{format_str}" '
        f'--connect-timeout {timeout} -m {timeout} "{url}"'
    )
    raw_output = source_node.cmd(cmd)

    parsed = parse_curl_output(raw_output)

    return HTTPMeasurement(
        source=src_name,
        target=tgt_name,
        url=url,
        http_status=parsed['http_status'],
        response_time_ms=parsed['response_time_ms'],
        connect_time_ms=parsed['connect_time_ms'],
        success=parsed['success'],
        raw_output=raw_output.strip(),
    )
