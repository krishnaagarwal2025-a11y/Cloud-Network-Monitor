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
import sys
import math
from src.traffic.process import start_service, run_probe
from typing import Optional, Dict, Any


@dataclass
class HTTPMeasurement:
    source: str
    target: str
    url: str
    http_status: int
    response_time_ms: Optional[float]
    connect_time_ms: Optional[float]
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


def parse_curl_output(raw_output: str) -> Dict[str, Any]:
    """
    Parse curl formatted output:
    status=200|time_total=0.012345|time_connect=0.004567
    """
    http_status = 0
    response_time_ms = None
    connect_time_ms = None

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
            response_time_ms = None

    match_connect = re.search(r'time_connect=([0-9.]+)', raw_output)
    if match_connect:
        try:
            connect_time_ms = round(float(match_connect.group(1)) * 1000.0, 3)
        except ValueError:
            connect_time_ms = None

    for name, value in [('total', response_time_ms), ('connect', connect_time_ms)]:
        if value is not None and not math.isfinite(value):
            if name == 'total':
                response_time_ms = None
            else:
                connect_time_ms = None
    success = (200 <= http_status < 400 and response_time_ms is not None
               and connect_time_ms is not None)

    return {
        'http_status': http_status,
        'response_time_ms': response_time_ms,
        'connect_time_ms': connect_time_ms,
        'success': success,
    }


def start_http_server(host_node, port: int = 8080):
    """Start an owned child serving a fixed, non-sensitive response directory."""
    import tempfile
    from pathlib import Path
    directory = tempfile.TemporaryDirectory(prefix='cloud-monitor-http-')
    try:
        Path(directory.name, 'index.html').write_text('cloud-network-monitor OK\n')
        service = start_service(host_node, [sys.executable, '-m', 'http.server',
                                str(port), '--bind', host_node.IP(),
                                '--directory', directory.name], port)
        service.directory = directory
        return service
    except BaseException:
        directory.cleanup()
        raise


def stop_http_server(host_node, service):
    """Stop only the child returned by start_http_server."""
    service.stop()
    service.directory.cleanup()


def measure_http(
    source_node,
    url: str,
    target_name: str = "",
    timeout: int = 3,
) -> HTTPMeasurement:
    """
    Probe the HTTP URL from source_node using curl and return an HTTPMeasurement.
    """
    if timeout <= 0:
        raise ValueError('HTTP timeout must be positive')
    src_name = getattr(source_node, 'name', str(source_node))
    tgt_name = target_name or url

    # Format output with status, total time, and connect time
    format_str = "status=%{http_code}|time_total=%{time_total}|time_connect=%{time_connect}"
    argv = ['curl', '--silent', '--show-error', '--noproxy', '*',
            '-o', '/dev/null', '-w', format_str, '--connect-timeout', str(timeout),
            '-m', str(timeout), url]
    raw_output, exit_code, error = run_probe(source_node, argv, timeout + 2)
    parsed = parse_curl_output(raw_output)
    parsed['success'] = parsed['success'] and exit_code == 0 and not error
    if not parsed['success'] and not error:
        error = f"HTTP status {parsed['http_status']}"

    return HTTPMeasurement(
        source=src_name,
        target=tgt_name,
        url=url,
        http_status=parsed['http_status'],
        response_time_ms=parsed['response_time_ms'],
        connect_time_ms=parsed['connect_time_ms'],
        success=parsed['success'],
        raw_output=raw_output.strip(),
        exit_code=exit_code,
        error_message=error,
    )
