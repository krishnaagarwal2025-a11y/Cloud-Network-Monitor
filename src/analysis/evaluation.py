"""Cycle-level detector evaluation; ground truth never enters inference."""

ALGORITHMS = ('threshold', 'ewma', 'zscore')


def evaluate(cycles, events):
    result = {'unit': 'one complete collection cycle', 'detectors': {}, 'localisation': {}}
    for algorithm in ALGORITHMS:
        counts = dict(true_positive=0, false_positive=0, true_negative=0, false_negative=0)
        delays = {}
        for cycle in cycles:
            if cycle.get('warmup'):
                continue
            positive = cycle['condition'] not in ('baseline', 'recovery')
            detected = cycle['analysis']['anomalies']['by_detector'][algorithm] > 0
            key = ('true_' if detected == positive else 'false_') + ('positive' if detected else 'negative')
            counts[key] += 1
            if positive and detected and cycle['scenario'] not in delays:
                event = next(e for e in events if e['scenario'] == cycle['scenario'] and e['state'] == 'applied')
                delays[cycle['scenario']] = max(0, cycle['timestamp'] - event['timestamp'])
        tp, fp, tn, fn = (counts[k] for k in ('true_positive', 'false_positive', 'true_negative', 'false_negative'))
        result['detectors'][algorithm] = {
            **counts,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'false_positive_rate': fp / (fp + tn) if fp + tn else None,
            'detection_delay_seconds': delays,
        }
    for cycle in cycles:
        ground_truth = cycle.get('segment') if cycle['condition'] not in ('baseline', 'recovery') else None
        inference = cycle['analysis']['localisation']
        if ground_truth:
            candidates = inference['candidate_segments']
            outcome = ('exact_match' if candidates == [ground_truth] else
                       'ambiguous_contains_target' if ground_truth in candidates else
                       'missed_target')
        else:
            outcome = ('correct_clean' if inference['status'] == 'healthy' else
                       'inconclusive' if inference['status'] == 'inconclusive' else 'false_positive')
        result['localisation'][outcome] = result['localisation'].get(outcome, 0) + 1
    result['limitations'] = [
        'Small controlled experiment; rates are not production accuracy estimates.',
        'Fault-cycle labels come from requested/applied conditions, not measured magnitude.',
        'Detection delay includes probe execution and collection cadence.',
        'Bandwidth-only faults may not be identifiable from ICMP paths.',
        'Candidate confidence is a single-fault heuristic, not a calibrated probability.',
    ]
    return result
