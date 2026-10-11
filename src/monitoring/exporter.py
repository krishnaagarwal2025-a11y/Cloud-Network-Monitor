"""Atomic, bounded-cardinality Prometheus snapshots of the latest probe cycle."""

from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
import math
import threading
import time

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily

from src.analysis.detector import CompositeAnomalyDetector
from src.analysis.localiser import FaultLocaliser, ALL_SEGMENTS


HOSTS = frozenset(('client', 'edge_gw', 'lb', 'web_srv', 'api_srv', 'auth_srv', 'db_srv'))
GROUPS = {'icmp': 'icmp', 'http': 'http', 'throughput': 'iperf'}
# Prometheus uses base units; JSON preserves the lab's milliseconds and Mbps.
METRICS = {
    'icmp': (
        ('cloud_ping_latency_seconds', 'Mean ping round-trip time.', 'rtt_avg_ms', 0.001),
        ('cloud_ping_min_seconds', 'Minimum ping round-trip time.', 'rtt_min_ms', 0.001),
        ('cloud_ping_max_seconds', 'Maximum ping round-trip time.', 'rtt_max_ms', 0.001),
        ('cloud_ping_variation_seconds', 'Ping RTT standard deviation (mdev).', 'rtt_mdev_ms', 0.001),
        ('cloud_packet_loss_ratio', 'Fraction of ping packets lost.', 'packet_loss_percent', 0.01),
    ),
    'http': (
        ('cloud_http_response_seconds', 'Mean HTTP response time; omitted if any request failed.', 'response_time_ms', 0.001),
        ('cloud_http_connect_seconds', 'Mean HTTP connection time; omitted if any request failed.', 'connect_time_ms', 0.001),
    ),
    'throughput': (
        ('cloud_throughput_bits_per_second', 'Receiver TCP throughput.', 'throughput_mbps', 1e6),
        ('cloud_tcp_retransmissions', 'Sender retransmissions during the last iperf test.', 'retransmits', 1),
        ('cloud_tcp_rtt_seconds', 'Sender mean RTT from the last iperf test.', 'mean_rtt_ms', 0.001),
        ('cloud_throughput_duration_seconds', 'Duration of the last iperf test.', 'duration_sec', 1),
    ),
}


class MeasurementCollector:
    """Collect independently of scrapes; swapping a snapshot prevents mixed cycles."""

    def __init__(self):
        self._lock = threading.Lock()
        self._report = None
        self._anomaly_summary = None
        self._localisation_summary = None
        self.detector = CompositeAnomalyDetector()
        self.localiser = FaultLocaliser()
        self._duration = 0.0
        self._attempt_time = 0.0
        self._errors = 0
        self._collection_ok = False
        self.registry = CollectorRegistry()
        self.registry.register(self)

    def update(self, report, duration=0.0):
        snapshot = deepcopy(report)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError('Collection duration must be finite and nonnegative')
        if not isinstance(snapshot.get('success'), bool):
            raise ValueError('Measurement cycle success must be boolean')
        for group in GROUPS:
            rows = snapshot[group]
            if not isinstance(rows, list):
                raise ValueError(f'{group} must be a list')
            for row in rows:
                if row['source'] not in HOSTS or row['target'] not in HOSTS:
                    raise ValueError('Only configured lab hosts may become metric labels')
                if not isinstance(row['success'], bool):
                    raise ValueError('Probe success must be boolean')
                if group == 'http' and (not isinstance(row.get('http_status'), int)
                                         or not 0 <= row['http_status'] <= 599):
                    raise ValueError('HTTP status must be a numeric code or zero')
                for key in ('timestamp', *(m[2] for m in METRICS[group])):
                    value = row.get(key)
                    if key == 'timestamp' and value is None:
                        raise ValueError('Every measurement requires a timestamp')
                    if value is not None and (not isinstance(value, (int, float))
                                              or not math.isfinite(value) or value < 0):
                        raise ValueError(f'Invalid measurement field: {key}')
        anomaly_summary = self.detector.analyze_cycle(snapshot)
        localisation_summary = self.localiser.localise(snapshot, anomaly_summary)
        with self._lock:
            self._report = snapshot
            self._anomaly_summary = anomaly_summary
            self._localisation_summary = localisation_summary
            self._duration = duration
            self._attempt_time = time.time()
            self._collection_ok = True

    def collection_failed(self, duration=0.0):
        """Clear old values on an exception; exporter HTTP remains available."""
        with self._lock:
            self._report = None
            self._anomaly_summary = None
            self._localisation_summary = None
            self._duration = duration
            self._attempt_time = time.time()
            self._collection_ok = False
            self._errors += 1

    def collect(self):
        with self._lock:
            report = self._report
            anomaly_summary = self._anomaly_summary
            localisation_summary = self._localisation_summary
            duration, timestamp = self._duration, self._attempt_time
            ok, errors = self._collection_ok, self._errors
        yield GaugeMetricFamily('cloud_collection_success',
                               'One if the latest collection completed, including failed probes.',
                               value=int(ok))
        yield GaugeMetricFamily('cloud_collection_timestamp_seconds',
                               'Unix time of the latest collection attempt completion.', value=timestamp)
        yield GaugeMetricFamily('cloud_collection_duration_seconds',
                               'Wall time of the latest collection attempt.', value=duration)
        yield CounterMetricFamily('cloud_collection_errors',
                                 'Collection exceptions since exporter startup.', value=errors)
        if report is None:
            return
        yield GaugeMetricFamily('cloud_measurement_cycle_success',
                               'One if all baseline measurement checks passed.',
                               value=int(report['success']))
        success = GaugeMetricFamily('cloud_probe_success', 'One if all probes of this type/path succeeded.',
                                    labels=['probe', 'source', 'target'])
        measured_at = GaugeMetricFamily('cloud_probe_timestamp_seconds', 'Unix time of the latest path probe.',
                                        labels=['probe', 'source', 'target'])
        http_ratio = GaugeMetricFamily('cloud_http_success_ratio', 'Successful HTTP requests / requests in cycle.',
                                        labels=['source', 'target'])
        http_status = GaugeMetricFamily('cloud_http_status_code', 'Status of the last HTTP request; zero if unavailable.',
                                         labels=['source', 'target'])
        for group, probe in GROUPS.items():
            paths = defaultdict(list)
            for row in report[group]:
                paths[(row['source'], row['target'])].append(row)
            families = [(GaugeMetricFamily(name, help_text, labels=['source', 'target']), field, scale)
                        for name, help_text, field, scale in METRICS[group]]
            for path, rows in sorted(paths.items()):
                path_ok = all(r['success'] for r in rows)
                success.add_metric([probe, *path], int(path_ok))
                measured_at.add_metric([probe, *path], max(r['timestamp'] for r in rows))
                if group == 'http':
                    http_ratio.add_metric(list(path), sum(r['success'] for r in rows) / len(rows))
                    http_status.add_metric(list(path), rows[-1]['http_status'])
                for family, field, scale in families:
                    values = [r.get(field) for r in rows]
                    # Loss is meaningful even with zero replies; missing values remain absent.
                    if all(v is not None for v in values) and (path_ok or field == 'packet_loss_percent'):
                        family.add_metric(list(path), sum(values) / len(values) * scale)
            yield from (family for family, _, _ in families)
        yield success
        yield measured_at
        yield http_ratio
        yield http_status

        if anomaly_summary is not None:
            yield GaugeMetricFamily(
                'cloud_anomaly_detected',
                'One if any statistical or threshold anomaly was detected in cycle.',
                value=int(anomaly_summary.get('anomaly_detected', False)),
            )
            yield GaugeMetricFamily(
                'cloud_anomalies_total',
                'Total number of detected anomalies across all algorithms.',
                value=anomaly_summary.get('total_anomalies', 0),
            )
            anom_by_det = GaugeMetricFamily(
                'cloud_anomaly_by_detector',
                'Anomalies broken down by detector algorithm.',
                labels=['detector'],
            )
            for det_name, count in sorted(anomaly_summary.get('by_detector', {}).items()):
                anom_by_det.add_metric([det_name], count)
            yield anom_by_det

        if localisation_summary is not None:
            yield GaugeMetricFamily(
                'cloud_fault_detected',
                'One if a faulty segment was localized, zero if network is healthy.',
                value=int(localisation_summary.status != 'healthy'),
            )
            fault_cand = GaugeMetricFamily(
                'cloud_fault_candidate',
                'One if segment is an inferred faulty segment candidate.',
                labels=['segment'],
            )
            fault_conf = GaugeMetricFamily(
                'cloud_fault_confidence',
                'Confidence score of segment fault localisation (0.0 to 1.0).',
                labels=['segment'],
            )
            candidates = set(localisation_summary.candidate_segments)
            for seg in ALL_SEGMENTS:
                is_cand = 1 if seg in candidates else 0
                fault_cand.add_metric([seg], is_cand)
                conf = localisation_summary.confidence if is_cand else 0.0
                fault_conf.add_metric([seg], conf)
            yield fault_cand
            yield fault_conf



@contextmanager
def metrics_server(collector, port=9200):
    """Expose localhost only; an occupied port raises without touching its owner."""
    server, thread = start_http_server(port, addr='127.0.0.1', registry=collector.registry)
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
