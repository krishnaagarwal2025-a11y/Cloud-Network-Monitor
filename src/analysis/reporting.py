"""Portable experiment tables and vector charts, without plotting dependencies."""

from collections import defaultdict
from html import escape
import json
import math
from pathlib import Path

COLORS = ('#2563eb', '#dc2626', '#16a34a', '#9333ea', '#ea580c', '#0891b2', '#475569')


def line_chart(title, unit, traces, conditions, mode):
    """Preserve null gaps; plot collection index rather than imply uniform time."""
    width, height = 1100, 410
    left, top, right, bottom = 75, 65, 35, 105
    w, h = width - left - right, height - top - bottom
    count = max((len(values) for values in traces.values()), default=0)
    finite = [v for values in traces.values() for v in values
              if v is not None and isinstance(v, (int, float)) and math.isfinite(v)]
    maximum = max(finite, default=1) * 1.12 or 1
    x = lambda i: left + w * i / max(1, count - 1)
    y = lambda v: top + h * (1 - v / maximum)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="{escape(title)} ({escape(mode)})">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="{left}" y="26" font-size="20" font-family="sans-serif">{escape(title)}</text>',
             f'<text x="{left}" y="47" font-size="12" fill="#475569">{escape(mode)} · {escape(unit)} · gaps mean unavailable</text>']
    for i, condition in enumerate(conditions):
        if condition not in ('baseline', 'recovery'):
            step = w / max(1, count - 1)
            start = max(left, x(i) - step / 2)
            parts.append(f'<rect x="{start:.2f}" y="{top}" width="{min(step, left + w - start):.2f}" '
                         f'height="{h}" fill="#fef3c7"/>')
    for tick in range(5):
        value = maximum * tick / 4
        yy = y(value)
        parts.append(f'<line x1="{left}" x2="{left+w}" y1="{yy:.2f}" y2="{yy:.2f}" stroke="#e2e8f0"/>')
        parts.append(f'<text x="{left-8}" y="{yy+4:.2f}" text-anchor="end" font-size="11">{value:.2f}</text>')
    for index in sorted(set(round(i * max(0, count - 1) / 6) for i in range(7))):
        parts.append(f'<text x="{x(index):.2f}" y="{top+h+22}" text-anchor="middle" font-size="11">{index+1}</text>')
    parts.append(f'<text x="{left+w/2}" y="{top+h+42}" text-anchor="middle" font-size="12">Collection cycle (not evenly spaced time)</text>')
    for number, (label, values) in enumerate(traces.items()):
        color = COLORS[number % len(COLORS)]
        points = []
        def flush():
            if points:
                parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2"/>')
                points.clear()
        for index, value in enumerate(values):
            if value is None or not math.isfinite(value):
                flush()
            else:
                points.append(f'{x(index):.2f},{y(value):.2f}')
                parts.append(f'<circle cx="{x(index):.2f}" cy="{y(value):.2f}" r="2.5" fill="{color}"/>')
        flush()
        xx, yy = left + (number % 3) * 330, height - 42 + (number // 3) * 19
        parts.append(f'<text x="{xx}" y="{yy}" fill="{color}" font-size="12">{escape(label)}</text>')
    parts.append('</svg>')
    return '\n'.join(parts)


def write_report(directory):
    directory = Path(directory)
    experiment = json.loads((directory / 'experiment.json').read_text())
    mode = experiment['mode']
    cycles = experiment['cycles']
    conditions = [c['condition'] for c in cycles]
    measurements = [json.loads((directory / c['report']).read_text()) for c in cycles]
    groups = {
        'latency': ('Round-trip latency', 'milliseconds', 'icmp', 'rtt_avg_ms'),
        'loss': ('Packet loss', 'percent', 'icmp', 'packet_loss_percent'),
        'throughput': ('TCP throughput', 'Mbps', 'throughput', 'throughput_mbps'),
        'http': ('HTTP response time', 'milliseconds', 'http', 'response_time_ms'),
    }
    images = []
    for name, (title, unit, group, field) in groups.items():
        paths = sorted({(r['source'], r['target']) for report in measurements for r in report[group]})
        traces = {}
        for path in paths:
            values = []
            for report in measurements:
                rows = [r for r in report[group] if (r['source'], r['target']) == path]
                available = [r.get(field) for r in rows]
                values.append(sum(available) / len(available) if available and all(v is not None for v in available) else None)
            traces[f'{path[0]} → {path[1]}'] = values
        image = line_chart(title, unit, traces, conditions, mode)
        (directory / f'{name}.svg').write_text(image)
        images.append(image)
    evaluation = experiment.get('evaluation', {})
    lines = [f'# Experiment report: {mode}', '',
             f'Acceptance result: **{"PASS" if experiment["success"] else "FAILED / INCOMPLETE"}**', '',
             '**Synthetic simulation is not evidence of Mininet or kernel fault acceptance.**' if mode != 'live_mininet'
             else 'All plotted values come from real traffic measurements saved by this run.', '',
             'Yellow chart bands mark requested/applied fault cycles, not independent detector output.', '',
             '## Per-detector evaluation', '',
             '| Detector | TP | FP | TN | FN | Precision | Recall |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    def number(value):
        return 'unknown' if value is None else f'{value:.3f}'
    for detector, stats in evaluation.get('detectors', {}).items():
        lines.append(f'| {detector} | {stats["true_positive"]} | {stats["false_positive"]} | '
                     f'{stats["true_negative"]} | {stats["false_negative"]} | '
                     f'{number(stats["precision"])} | {number(stats["recall"])} |')
    lines.extend(['', 'Warm-up cycles are excluded from detector rates. HTTP requests in one collection are',
                  'aggregated before updating statistical baselines. Confusion labels are controlled',
                  'experiment conditions, not a claim that every requested fault produced the same severity.', '',
                  '## Recovery and localization', '',
                  '| Scenario | Condition | Candidate sets during fault | Recovery verified |',
                  '| --- | --- | --- | --- |'])
    for scenario in experiment['settings']['scenarios']:
        candidates = {tuple(c['analysis']['localisation']['candidate_segments']) for c in cycles
                      if c['scenario'] == scenario['name'] and c['condition'] != 'recovery'}
        recovery = any(e['scenario'] == scenario['name'] and e['state'] == 'recovery_verified'
                       for e in experiment['events'])
        lines.append(f'| {scenario["name"]} | {scenario["condition"]} | '
                     f'{escape(str(sorted(candidates)))} | {recovery} |')
    lines.extend(['', '## Limitations', ''])
    lines.extend('- ' + item for item in evaluation.get('limitations', []))
    lines.extend(['- Original JSON records and event timestamps are the authoritative evidence.',
                  '- Simulation detection delays measure processing cadence, not physical network response.',
                  '- Live kernel acceptance and measured recovery require root and successful cleanup.', '',
                  '## Files', '', '`experiment.json` contains settings, events, analysis, queries, errors and cleanup.',
                  '`traffic_metrics_*.json` contains each immutable measurement cycle.',
                  '`latency.svg`, `loss.svg`, `throughput.svg`, `http.svg` are vector charts.',
                  '`report.html` combines the charts for viewing or printing.', ''])
    (directory / 'summary.md').write_text('\n'.join(lines))
    style = 'body{font:16px system-ui;margin:32px auto;max-width:1120px;color:#172033}svg{width:100%;margin:15px 0;border:1px solid #ddd}code{background:#eef2f6;padding:4px}h1{font-size:28px}'
    html = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Experiment report</title>'
            f'<style>{style}</style><h1>Cloud Network Monitor — {escape(mode)}</h1>'
            f'<p>Acceptance: {"PASS" if experiment["success"] else "FAILED / INCOMPLETE"}. '
            'Yellow bands mark fault cycles; gaps are missing observations.</p>'
            '<p>Simulation validates software behavior. It does not establish live network performance.</p>'
            + ''.join(images) + '<p>Full evaluation: <a href="summary.md">summary.md</a>. '
            'Raw evidence: <a href="experiment.json">experiment.json</a>.</p></html>')
    (directory / 'report.html').write_text(html)
