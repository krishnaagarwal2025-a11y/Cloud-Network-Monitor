"""
Network Tomography & Segment-Wise Fault Localisation
---------------------------------------------------
Infers likely faulty network segments from end-to-end path measurements.
Uses Boolean Tomography and Differential Latency Analysis across the
multi-tier cloud topology.

Topology Segments:
  S1: seg_client_edge (s_client <-> s_edge)
  S2: seg_edge_lb     (s_edge <-> s_lb)
  S3: seg_lb_app      (s_lb <-> s_app)
  S4: seg_app_db      (s_app <-> s_db)

Path Matrix:
  P1 (client -> edge_gw): [S1]
  P2 (client -> web_srv): [S1, S2, S3]
  P3 (client -> api_srv): [S1, S2, S3]
  P4 (client -> db_srv) : [S1, S2, S3, S4]
  P5 (web_srv -> db_srv): [S4]

Tomography Principles:
1. Axiom of Good Paths: Any segment traversed by a healthy path is healthy.
   (Healthy link elimination)
2. Intersection of Bad Paths: A fault must reside in the intersection of
   segments traversed by all degraded paths.
3. Ambiguity Resolution: If multiple candidate segments cannot be distinguished
   by available paths (e.g. S2 vs S3 under default probes), the algorithm
   explicitly reports ambiguity and tied candidate set rather than guessing.
"""

from dataclasses import dataclass, asdict
import math
import time
from typing import Dict, List, Optional, Set, Tuple, Any

from src.topology.cloud_topology import SEGMENTS


# Map of probed paths to the segments they traverse
PATH_SEGMENT_MAP: Dict[Tuple[str, str], List[str]] = {
    ('client', 'edge_gw'): ['seg_client_edge'],
    ('client', 'web_srv'): ['seg_client_edge', 'seg_edge_lb', 'seg_lb_app'],
    ('client', 'api_srv'): ['seg_client_edge', 'seg_edge_lb', 'seg_lb_app'],
    ('client', 'db_srv'):  ['seg_client_edge', 'seg_edge_lb', 'seg_lb_app', 'seg_app_db'],
    ('web_srv', 'db_srv'): ['seg_app_db'],
    # Optional disambiguation paths if probed:
    ('edge_gw', 'web_srv'): ['seg_edge_lb', 'seg_lb_app'],
    ('lb', 'web_srv'):      ['seg_lb_app'],
}

ALL_SEGMENTS = list(SEGMENTS.keys())


@dataclass(frozen=True)
class LocalisationResult:
    timestamp: float
    candidate_segments: List[str]
    confidence: float                  # 1.0 = unambiguous, 0.5 = 2-way tie, etc.
    is_ambiguous: bool
    status: str                        # 'healthy', 'localised', 'ambiguous', 'inconclusive'
    explanation: str
    healthy_paths: List[str]
    anomalous_paths: List[str]
    eliminated_segments: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def evaluate_ground_truth(self, injected_segment: Optional[str]) -> Dict[str, Any]:
        """
        Compare inferred candidates against the known injected fault.
        Returns evaluation dict with classification.
        """
        if injected_segment is None:
            correct = (len(self.candidate_segments) == 0)
            return {
                'injected_segment': None,
                'classification': 'correct_clean' if correct else 'false_positive',
                'matched': correct,
            }

        if injected_segment in self.candidate_segments:
            if not self.is_ambiguous:
                classification = 'exact_match'
            else:
                classification = 'ambiguous_contains_target'
            matched = True
        else:
            classification = 'missed_target' if self.candidate_segments else 'false_negative'
            matched = False

        return {
            'injected_segment': injected_segment,
            'candidate_segments': self.candidate_segments,
            'classification': classification,
            'matched': matched,
            'confidence': self.confidence,
            'is_ambiguous': self.is_ambiguous,
        }


class FaultLocaliser:
    """
    Infers likely faulty segments using path routing matrix and probe states.
    """

    def __init__(self,
                 latency_spike_threshold_ms: float = 20.0,
                 loss_threshold_percent: float = 1.0):
        self.latency_spike_threshold_ms = latency_spike_threshold_ms
        self.loss_threshold_percent = loss_threshold_percent

    def localise(self,
                 report: Dict[str, Any],
                 anomaly_report: Optional[Dict[str, Any]] = None) -> LocalisationResult:
        """
        Localise fault given a measurement cycle report and optional anomaly analysis.
        """
        now = report.get('timestamp', time.time())

        # 1. Determine health state of each probed path
        healthy_paths = []
        anomalous_paths = []

        # Use anomaly report path findings if provided
        anomalous_from_detector: Set[Tuple[str, str]] = set()
        if anomaly_report and anomaly_report.get('anomalies'):
            for a in anomaly_report['anomalies']:
                anomalous_from_detector.add((a['source'], a['target']))

        for probe in report.get('icmp', []):
            src, tgt = probe['source'], probe['target']
            path_key = (src, tgt)
            if path_key not in PATH_SEGMENT_MAP:
                continue

            path_str = f'{src}->{tgt}'
            success = probe.get('success', False)
            loss = probe.get('packet_loss_percent', 0.0)
            rtt = probe.get('rtt_avg_ms')

            # Path is anomalous if:
            # - flagged by anomaly detector
            # - probe failed
            # - packet loss > threshold
            # - latency exceeds baseline by threshold
            is_bad = (path_key in anomalous_from_detector) or (not success)
            if loss is not None and loss >= self.loss_threshold_percent:
                is_bad = True

            # Check latency elevation against nominal
            nominal_rtt = {
                ('client', 'edge_gw'): 10.0,
                ('client', 'web_srv'): 16.0,
                ('client', 'api_srv'): 16.0,
                ('client', 'db_srv'): 18.0,
                ('web_srv', 'db_srv'): 2.0,
            }.get(path_key, 20.0)

            if rtt is not None and (rtt - nominal_rtt) >= self.latency_spike_threshold_ms:
                is_bad = True

            if is_bad:
                anomalous_paths.append(path_str)
            else:
                healthy_paths.append(path_str)

        # 2. Boolean Tomography Inference
        if not anomalous_paths:
            return LocalisationResult(
                timestamp=now,
                candidate_segments=[],
                confidence=1.0,
                is_ambiguous=False,
                status='healthy',
                explanation='All network paths operating normally; no faults detected.',
                healthy_paths=healthy_paths,
                anomalous_paths=[],
                eliminated_segments=ALL_SEGMENTS,
            )

        # Elimination Rule: Any segment on a healthy path is healthy
        eliminated_segments: Set[str] = set()
        for p_str in healthy_paths:
            src, tgt = p_str.split('->')
            for seg in PATH_SEGMENT_MAP.get((src, tgt), []):
                eliminated_segments.add(seg)

        # Candidate Rule: Segments traversed by anomalous paths, minus eliminated
        candidate_sets = []
        for p_str in anomalous_paths:
            src, tgt = p_str.split('->')
            segs = set(PATH_SEGMENT_MAP.get((src, tgt), []))
            candidate_sets.append(segs)

        # Intersection of all bad paths
        if candidate_sets:
            common_bad = set.intersection(*candidate_sets)
        else:
            common_bad = set()

        # Candidates are common bad segments that were not proven healthy
        candidates = list(common_bad - eliminated_segments)

        # If strict intersection is empty (e.g. multiple distinct faults or noise),
        # use union minus eliminated
        if not candidates and candidate_sets:
            union_bad = set.union(*candidate_sets)
            candidates = list(union_bad - eliminated_segments)

        candidates.sort()

        if len(candidates) == 0:
            status = 'inconclusive'
            confidence = 0.0
            is_ambiguous = True
            explanation = 'Inconsistent measurements: all candidate segments eliminated by healthy paths.'
        elif len(candidates) == 1:
            status = 'localised'
            confidence = 1.0
            is_ambiguous = False
            explanation = (f'Fault definitively localised to segment {candidates[0]} '
                           f'(supported by {len(anomalous_paths)} degraded paths, '
                           f'{len(eliminated_segments)} segments verified healthy).')
        else:
            status = 'ambiguous'
            confidence = round(1.0 / len(candidates), 2)
            is_ambiguous = True
            explanation = (f'Fault localised to candidate set {candidates}; '
                           f'segments are topologically indistinguishable under current probe matrix.')

        return LocalisationResult(
            timestamp=now,
            candidate_segments=candidates,
            confidence=confidence,
            is_ambiguous=is_ambiguous,
            status=status,
            explanation=explanation,
            healthy_paths=healthy_paths,
            anomalous_paths=anomalous_paths,
            eliminated_segments=sorted(list(eliminated_segments)),
        )
