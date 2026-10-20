"""Read explicit lab thresholds without introducing configuration dependencies."""

from copy import deepcopy
import json
from pathlib import Path

from src.analysis.detector import CompositeAnomalyDetector
from src.analysis.localiser import FaultLocaliser, PATH_SEGMENT_MAP

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / 'config' / 'analysis.json'


def load_analysis(path=None):
    settings = json.loads(Path(path or DEFAULT_CONFIG).read_text())
    allowed = {'thresholds', 'ewma_alpha', 'ewma_k', 'zscore_window', 'zscore_thresh', 'localisation'}
    if not isinstance(settings, dict) or set(settings) - allowed:
        raise ValueError('Unknown analysis configuration keys')
    detector_settings = deepcopy(settings)
    localiser_settings = detector_settings.pop('localisation', {})
    thresholds = detector_settings.get('thresholds', {})
    if set(thresholds) - {'latency_ms', 'packet_loss_ratio', 'http_response_time_ms', 'min_throughput_mbps'}:
        raise ValueError('Unknown threshold configuration keys')
    if 'latency_ms' in thresholds:
        paths = {}
        for name, value in thresholds['latency_ms'].items():
            key = 'default' if name == 'default' else tuple(name.split('->'))
            if key != 'default' and key not in PATH_SEGMENT_MAP:
                raise ValueError(f'Unknown latency path: {name}')
            paths[key] = value
        thresholds['latency_ms'] = paths
    # Existing constructors validate numeric values and parameter relationships.
    return (CompositeAnomalyDetector(**detector_settings),
            FaultLocaliser(**localiser_settings), settings)
