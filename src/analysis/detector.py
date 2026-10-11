"""
Statistical and Threshold-based Network Anomaly Detection
---------------------------------------------------------
Implements three complementary anomaly detection strategies:
1. Static / Configurable Thresholds: bounds on latency, loss, response time, throughput.
2. EWMA (Exponentially Weighted Moving Average): adaptive trend tracking with dynamic variance.
3. Z-score: sliding-window statistical deviation detection.

Key features:
- Never treats missing data (None) as zero latency or zero loss.
- Protects against division-by-zero when baseline variance is zero.
- Enforces minimum sample warm-up before statistical alerting.
- Bounded history windows to avoid unbounded memory growth.
- Outputs structured AnomalyRecord objects with timestamps and severity.
"""

from collections import deque
from dataclasses import dataclass, asdict
import math
import time
from typing import Dict, List, Optional, Tuple, Any


@dataclass(frozen=True)
class AnomalyRecord:
    timestamp: float
    detector: str           # 'threshold', 'ewma', 'zscore'
    metric: str             # 'latency', 'packet_loss', 'http_response_time', 'throughput', 'availability'
    source: str
    target: str
    observed_value: Optional[float]
    expected_value: Optional[float]
    score: float            # deviation, Z-score, or threshold excess ratio
    severity: str           # 'warning', 'critical'
    description: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ThresholdDetector:
    """
    Evaluates individual measurements against configurable domain thresholds.
    """

    DEFAULT_THRESHOLDS = {
        # Max acceptable ping latency in milliseconds per path
        'latency_ms': {
            ('client', 'edge_gw'): 25.0,    # baseline ~10ms
            ('client', 'web_srv'): 40.0,    # baseline ~16-18ms
            ('client', 'api_srv'): 40.0,    # baseline ~16-18ms
            ('client', 'db_srv'): 45.0,     # baseline ~18-20ms
            ('web_srv', 'db_srv'): 10.0,    # baseline ~2ms
            'default': 50.0,
        },
        # Max packet loss ratio (0.0 to 1.0)
        'packet_loss_ratio': 0.01,          # baseline 0%
        # Max HTTP response time in milliseconds
        'http_response_time_ms': 80.0,      # baseline ~30-40ms
        # Min throughput in Mbps
        'min_throughput_mbps': 50.0,        # baseline ~90-95 Mbps
    }

    def __init__(self, thresholds: Optional[Dict[str, Any]] = None):
        self.thresholds = dict(self.DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)
        limits = self.thresholds['latency_ms']
        if not isinstance(limits, dict) or not limits:
            raise ValueError('latency_ms must be a nonempty path threshold map')
        values = [*limits.values(), self.thresholds['http_response_time_ms'],
                  self.thresholds['min_throughput_mbps']]
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError('Latency, HTTP and throughput thresholds must be finite and positive')
        loss = self.thresholds['packet_loss_ratio']
        if not isinstance(loss, (int, float)) or not math.isfinite(loss) or not 0 <= loss <= 1:
            raise ValueError('packet_loss_ratio must be in [0, 1]')

    def evaluate_cycle(self, report: Dict[str, Any]) -> List[AnomalyRecord]:
        anomalies = []
        now = report.get('timestamp', time.time())

        # 1. ICMP checks
        for probe in report.get('icmp', []):
            src, tgt = probe['source'], probe['target']
            loss_percent = probe.get('packet_loss_percent')
            if loss_percent is not None and loss_percent > (self.thresholds['packet_loss_ratio'] * 100):
                severity = 'critical' if loss_percent >= 50.0 else 'warning'
                anomalies.append(AnomalyRecord(
                    timestamp=now,
                    detector='threshold',
                    metric='packet_loss',
                    source=src,
                    target=tgt,
                    observed_value=float(loss_percent),
                    expected_value=self.thresholds['packet_loss_ratio'] * 100,
                    score=loss_percent / 100.0,
                    severity=severity,
                    description=f'Packet loss {loss_percent:.1f}% exceeds threshold on {src}->{tgt}',
                ))

            rtt = probe.get('rtt_avg_ms')
            if rtt is not None and probe.get('success', False):
                thresh_map = self.thresholds['latency_ms']
                limit = thresh_map.get((src, tgt), thresh_map.get('default', 50.0))
                if rtt > limit:
                    severity = 'critical' if rtt >= (limit * 2.0) else 'warning'
                    anomalies.append(AnomalyRecord(
                        timestamp=now,
                        detector='threshold',
                        metric='latency',
                        source=src,
                        target=tgt,
                        observed_value=float(rtt),
                        expected_value=float(limit),
                        score=round(rtt / limit, 2),
                        severity=severity,
                        description=f'Ping latency {rtt:.2f}ms exceeds threshold {limit}ms on {src}->{tgt}',
                    ))
            elif not probe.get('success', False) and (loss_percent is None or loss_percent < 100.0):
                anomalies.append(AnomalyRecord(
                    timestamp=now,
                    detector='threshold',
                    metric='availability',
                    source=src,
                    target=tgt,
                    observed_value=0.0,
                    expected_value=1.0,
                    score=1.0,
                    severity='critical',
                    description=f'ICMP probe failed completely on {src}->{tgt}',
                ))

        # 2. HTTP checks
        for probe in report.get('http', []):
            src, tgt = probe['source'], probe['target']
            resp_time = probe.get('response_time_ms')
            status = probe.get('http_status')
            success = probe.get('success', False)

            if not success or status != 200:
                anomalies.append(AnomalyRecord(
                    timestamp=now,
                    detector='threshold',
                    metric='availability',
                    source=src,
                    target=tgt,
                    observed_value=float(status if status is not None else 0),
                    expected_value=200.0,
                    score=1.0,
                    severity='critical',
                    description=f'HTTP request failed (status={status}) on {src}->{tgt}',
                ))
            elif resp_time is not None:
                limit = self.thresholds['http_response_time_ms']
                if resp_time > limit:
                    severity = 'critical' if resp_time >= (limit * 2.0) else 'warning'
                    anomalies.append(AnomalyRecord(
                        timestamp=now,
                        detector='threshold',
                        metric='http_response_time',
                        source=src,
                        target=tgt,
                        observed_value=float(resp_time),
                        expected_value=float(limit),
                        score=round(resp_time / limit, 2),
                        severity=severity,
                        description=f'HTTP response time {resp_time:.2f}ms exceeds threshold {limit}ms',
                    ))

        # 3. Throughput checks
        for probe in report.get('throughput', []):
            src, tgt = probe['source'], probe['target']
            tp = probe.get('throughput_mbps')
            success = probe.get('success', False)

            if not success:
                anomalies.append(AnomalyRecord(
                    timestamp=now,
                    detector='threshold',
                    metric='availability',
                    source=src,
                    target=tgt,
                    observed_value=0.0,
                    expected_value=self.thresholds['min_throughput_mbps'],
                    score=1.0,
                    severity='critical',
                    description=f'Throughput probe failed on {src}->{tgt}',
                ))
            elif tp is not None:
                limit = self.thresholds['min_throughput_mbps']
                if tp < limit:
                    score = round(1.0 - (tp / limit), 2)
                    severity = 'critical' if tp < (limit * 0.5) else 'warning'
                    anomalies.append(AnomalyRecord(
                        timestamp=now,
                        detector='threshold',
                        metric='throughput',
                        source=src,
                        target=tgt,
                        observed_value=float(tp),
                        expected_value=float(limit),
                        score=score,
                        severity=severity,
                        description=f'Throughput {tp:.2f} Mbps below threshold {limit} Mbps on {src}->{tgt}',
                    ))

        return anomalies


class EWMADetector:
    """
    Exponentially Weighted Moving Average (EWMA) Anomaly Detector.
    Tracks adaptive metric mean and mean absolute deviation.
    """

    def __init__(self, alpha: float = 0.3, k_factor: float = 3.0,
                 min_samples: int = 3, min_delta: float = 2.0):
        if not 0.0 < alpha <= 1.0:
            raise ValueError('alpha must be in (0, 1]')
        if not math.isfinite(k_factor) or k_factor <= 0:
            raise ValueError('k_factor must be positive')
        if not isinstance(min_samples, int) or min_samples < 1 or not math.isfinite(min_delta) or min_delta <= 0:
            raise ValueError('min_samples and min_delta must be positive')
        self.alpha = alpha
        self.k_factor = k_factor
        self.min_samples = min_samples
        self.min_delta = min_delta
        # Series state: key -> {'mean': float, 'dev': float, 'count': int}
        self.series: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def update(self, key: Tuple[str, str, str], value: Optional[float],
               timestamp: float) -> Optional[AnomalyRecord]:
        """
        Update EWMA state and check for anomalies.
        key: (metric, source, target)
        value: float or None (skipped if None)
        """
        if value is None or not math.isfinite(value):
            return None

        metric, src, tgt = key
        state = self.series.get(key)
        if state is None:
            self.series[key] = {'mean': float(value), 'dev': 0.0, 'count': 1}
            return None

        state['count'] += 1
        mean = state['mean']
        dev = state['dev']
        delta = abs(value - mean)

        # Check for anomaly using PRIOR baseline before incorporating this point
        anomaly = None
        if state['count'] > self.min_samples:
            threshold = max(self.k_factor * dev, self.min_delta)
            is_anomaly = False
            if metric in ('latency', 'packet_loss', 'http_response_time'):
                # Latency/loss increases are anomalous
                is_anomaly = (value - mean) > threshold
            elif metric == 'throughput':
                # Throughput drops are anomalous
                is_anomaly = (mean - value) > threshold

            if is_anomaly:
                score = round(delta / threshold, 2)
                severity = 'critical' if score >= 2.0 else 'warning'
                anomaly = AnomalyRecord(
                    timestamp=timestamp,
                    detector='ewma',
                    metric=metric,
                    source=src,
                    target=tgt,
                    observed_value=float(value),
                    expected_value=round(mean, 3),
                    score=score,
                    severity=severity,
                    description=f'EWMA anomaly: {metric} on {src}->{tgt} is {value:.2f} (baseline mean={mean:.2f}, limit=+{threshold:.2f})',
                )

        # Only update baseline if NOT a severe outlier, or damp update
        # Adaptive update:
        effective_alpha = self.alpha if anomaly is None else self.alpha * 0.2
        state['mean'] = (effective_alpha * value) + ((1.0 - effective_alpha) * mean)
        state['dev'] = (effective_alpha * delta) + ((1.0 - effective_alpha) * dev)

        return anomaly

    def evaluate_cycle(self, report: Dict[str, Any]) -> List[AnomalyRecord]:
        anomalies = []
        now = report.get('timestamp', time.time())

        # ICMP
        for probe in report.get('icmp', []):
            src, tgt = probe['source'], probe['target']
            if probe.get('success', False):
                res = self.update(('latency', src, tgt), probe.get('rtt_avg_ms'), now)
                if res:
                    anomalies.append(res)
            loss = probe.get('packet_loss_percent')
            if loss is not None:
                res = self.update(('packet_loss', src, tgt), loss, now)
                if res:
                    anomalies.append(res)

        # HTTP
        for probe in report.get('http', []):
            src, tgt = probe['source'], probe['target']
            if probe.get('success', False):
                res = self.update(('http_response_time', src, tgt), probe.get('response_time_ms'), now)
                if res:
                    anomalies.append(res)

        # Throughput
        for probe in report.get('throughput', []):
            src, tgt = probe['source'], probe['target']
            if probe.get('success', False):
                res = self.update(('throughput', src, tgt), probe.get('throughput_mbps'), now)
                if res:
                    anomalies.append(res)

        return anomalies


class ZScoreDetector:
    """
    Sliding Window Z-score Anomaly Detector.
    Z = (X - mean) / std_dev
    """

    def __init__(self, window_size: int = 15, z_threshold: float = 3.0,
                 min_samples: int = 5, min_delta: float = 3.0):
        if not isinstance(window_size, int) or window_size < 3:
            raise ValueError('window_size must be at least 3')
        if not math.isfinite(z_threshold) or z_threshold <= 0:
            raise ValueError('z_threshold must be positive')
        if not isinstance(min_samples, int) or not 2 <= min_samples <= window_size:
            raise ValueError('min_samples must be between 2 and window_size')
        if not math.isfinite(min_delta) or min_delta <= 0:
            raise ValueError('min_delta must be finite and positive')
        self.window_size = window_size
        self.z_threshold = z_threshold
        self.min_samples = min_samples
        self.min_delta = min_delta
        # Series history: key -> deque of float values
        self.windows: Dict[Tuple[str, str, str], deque] = {}

    def update(self, key: Tuple[str, str, str], value: Optional[float],
               timestamp: float) -> Optional[AnomalyRecord]:
        if value is None or not math.isfinite(value):
            return None

        metric, src, tgt = key
        history = self.windows.setdefault(key, deque(maxlen=self.window_size))

        anomaly = None
        if len(history) >= self.min_samples:
            n = len(history)
            mean = sum(history) / n
            variance = sum((x - mean) ** 2 for x in history) / (n - 1)
            std_dev = math.sqrt(variance)

            diff = value - mean
            if std_dev > 1e-6:
                z = diff / std_dev
            else:
                # Zero baseline variance: check min_delta
                z = (diff / self.min_delta) if abs(diff) > self.min_delta else 0.0

            is_anomaly = False
            if metric in ('latency', 'packet_loss', 'http_response_time'):
                if z >= self.z_threshold and diff >= self.min_delta:
                    is_anomaly = True
            elif metric == 'throughput':
                if z <= -self.z_threshold and (-diff) >= self.min_delta:
                    is_anomaly = True

            if is_anomaly:
                score = round(abs(z), 2)
                severity = 'critical' if score >= (self.z_threshold * 1.5) else 'warning'
                anomaly = AnomalyRecord(
                    timestamp=timestamp,
                    detector='zscore',
                    metric=metric,
                    source=src,
                    target=tgt,
                    observed_value=float(value),
                    expected_value=round(mean, 3),
                    score=score,
                    severity=severity,
                    description=f'Z-score anomaly: {metric} on {src}->{tgt} Z={z:+.2f} (value={value:.2f}, baseline mean={mean:.2f}, std={std_dev:.2f})',
                )

        history.append(float(value))
        return anomaly

    def evaluate_cycle(self, report: Dict[str, Any]) -> List[AnomalyRecord]:
        anomalies = []
        now = report.get('timestamp', time.time())

        for probe in report.get('icmp', []):
            src, tgt = probe['source'], probe['target']
            if probe.get('success', False):
                res = self.update(('latency', src, tgt), probe.get('rtt_avg_ms'), now)
                if res:
                    anomalies.append(res)
            loss = probe.get('packet_loss_percent')
            if loss is not None:
                res = self.update(('packet_loss', src, tgt), loss, now)
                if res:
                    anomalies.append(res)

        for probe in report.get('http', []):
            src, tgt = probe['source'], probe['target']
            if probe.get('success', False):
                res = self.update(('http_response_time', src, tgt), probe.get('response_time_ms'), now)
                if res:
                    anomalies.append(res)

        for probe in report.get('throughput', []):
            src, tgt = probe['source'], probe['target']
            if probe.get('success', False):
                res = self.update(('throughput', src, tgt), probe.get('throughput_mbps'), now)
                if res:
                    anomalies.append(res)

        return anomalies


class CompositeAnomalyDetector:
    """
    Coordinates Threshold, EWMA, and Z-score detectors.
    Aggregates findings into comprehensive cycle reports.
    """

    def __init__(self,
                 thresholds: Optional[Dict[str, Any]] = None,
                 ewma_alpha: float = 0.3,
                 ewma_k: float = 3.0,
                 zscore_window: int = 15,
                 zscore_thresh: float = 3.0):
        self.threshold_detector = ThresholdDetector(thresholds)
        self.ewma_detector = EWMADetector(alpha=ewma_alpha, k_factor=ewma_k)
        self.zscore_detector = ZScoreDetector(window_size=zscore_window, z_threshold=zscore_thresh)

    def analyze_cycle(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze a single measurement cycle with all detectors.
        Returns a structured dictionary of results suitable for Prometheus export.
        """
        t_anomalies = self.threshold_detector.evaluate_cycle(report)
        e_anomalies = self.ewma_detector.evaluate_cycle(report)
        z_anomalies = self.zscore_detector.evaluate_cycle(report)

        all_anomalies = t_anomalies + e_anomalies + z_anomalies

        # Metric-level aggregates: e.g. latency_anomalies, loss_anomalies, etc.
        by_metric: Dict[str, int] = {}
        by_path: Dict[Tuple[str, str], int] = {}
        by_detector = {'threshold': len(t_anomalies), 'ewma': len(e_anomalies), 'zscore': len(z_anomalies)}

        for a in all_anomalies:
            by_metric[a.metric] = by_metric.get(a.metric, 0) + 1
            path = (a.source, a.target)
            by_path[path] = by_path.get(path, 0) + 1

        return {
            'timestamp': report.get('timestamp', time.time()),
            'anomaly_detected': len(all_anomalies) > 0,
            'total_anomalies': len(all_anomalies),
            'by_detector': by_detector,
            'by_metric': by_metric,
            'by_path': {f'{s}->{t}': cnt for (s, t), cnt in by_path.items()},
            'anomalies': [a.to_dict() for a in all_anomalies],
        }
