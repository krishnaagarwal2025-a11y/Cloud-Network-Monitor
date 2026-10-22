"""
Controlled Network Fault Injection via Linux tc / netem
--------------------------------------------------------
Applies and safely removes network impairments (latency, loss, bandwidth restrictions)
on specific Mininet segment links.

Safety principles:
- Only touches interfaces belonging to the specified segment link.
    - Tracks ownership and attempts restoration of declared baseline QoS.
- Verifies the impairment is active after applying.
- Cleans up in a finally block / context manager even on errors.
- Never runs destructive global qdisc resets.
"""

from contextlib import contextmanager
from dataclasses import dataclass, asdict
import time
import math
import re
from typing import Dict, Optional, Any, Tuple

from src.topology.cloud_topology import SEGMENTS


@dataclass(frozen=True)
class FaultInjectionRecord:
    timestamp: float
    segment: str
    interfaces: Tuple[str, str]
    fault_type: str             # 'latency', 'loss', 'bandwidth', 'combined'
    parameters: Dict[str, Any]
    original_config: Dict[str, Any]
    active: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class FaultInjector:
    """
    Manages controlled tc/netem impairment lifecycle on Mininet segment links.
    """

    def __init__(self, net=None):
        self.net = net
        # Active impairments: segment_name -> FaultInjectionRecord
        self.active_faults: Dict[str, FaultInjectionRecord] = {}

    def find_segment_link(self, segment_name: str):
        """Locate the Mininet link object for a named segment."""
        if segment_name not in SEGMENTS:
            raise ValueError(f'Unknown segment: {segment_name}. Known: {list(SEGMENTS.keys())}')
        if self.net is None:
            return None

        seg = SEGMENTS[segment_name]
        n1_name, n2_name = seg['node1'], seg['node2']

        for link in self.net.links:
            nodes = {link.intf1.node.name, link.intf2.node.name}
            if nodes == {n1_name, n2_name}:
                return link

        raise RuntimeError(f'Mininet link for {segment_name} ({n1_name} <-> {n2_name}) not found')

    def apply_fault(self, segment_name: str,
                    delay: Optional[str] = None,
                    loss: Optional[float] = None,
                    bw: Optional[float] = None) -> FaultInjectionRecord:
        """
        Apply controlled fault on the specified segment.
        delay: e.g. '50ms' (added to or overriding baseline delay)
        loss: packet loss percentage, e.g. 10.0 (10%)
        bw: bandwidth in Mbps, e.g. 10.0 (10 Mbps)
        """
        if segment_name in self.active_faults:
            raise RuntimeError(f'A fault is already active on {segment_name}; restore it first')

        if segment_name not in SEGMENTS:
            raise ValueError(f'Unknown segment: {segment_name}')

        if delay is loss is bw is None:
            raise ValueError('Specify at least one impairment')
        if delay is not None and (not isinstance(delay, str) or
                                  not re.fullmatch(r'(?:\d+(?:\.\d+)?)(?:us|ms|s)', delay)):
            raise ValueError('delay must be a nonnegative duration such as 50ms')
        for name, value, low, high in (('loss', loss, 0, 100), ('bw', bw, 0, 1000)):
            if value is not None and (isinstance(value, bool) or
                    not isinstance(value, (int, float)) or not math.isfinite(value) or
                    value < low or value > high or (name == 'bw' and value == 0)):
                raise ValueError(f'{name} must be finite and in '
                                 + ('[0, 100]' if name == 'loss' else '(0, 1000]'))

        baseline = dict(SEGMENTS[segment_name])
        target_delay = delay if delay is not None else baseline['delay']
        target_loss = loss if loss is not None else baseline['loss']
        target_bw = bw if bw is not None else baseline['bw']

        # Determine fault type description
        types = []
        if delay is not None:
            types.append('latency')
        if loss is not None:
            types.append('loss')
        if bw is not None:
            types.append('bandwidth')
        fault_type = '+'.join(types) if types else 'none'

        params = {'delay': target_delay, 'loss': target_loss, 'bw': target_bw}

        intf_names = ('sim-eth1', 'sim-eth2')
        link = self.find_segment_link(segment_name)
        if link is not None:
            intf1, intf2 = link.intf1, link.intf2
            intf_names = (intf1.name, intf2.name)

            # Retain restoration ownership before the first interface changes.
            pending = FaultInjectionRecord(time.time(), segment_name, intf_names,
                                           fault_type, params, baseline, True)
            self.active_faults[segment_name] = pending
            try:
                for intf in (intf1, intf2):
                    self._configure(intf, params)
            except BaseException as exc:
                try:
                    self.restore_fault(segment_name)
                except Exception as cleanup:
                    raise RuntimeError(f'Fault application failed: {exc}; rollback failed: {cleanup}') from exc
                raise

        record = FaultInjectionRecord(
            timestamp=time.time(),
            segment=segment_name,
            interfaces=intf_names,
            fault_type=fault_type,
            parameters=params,
            original_config=baseline,
            active=True,
        )
        self.active_faults[segment_name] = record
        return record

    def restore_fault(self, segment_name: str) -> bool:
        """Restore original baseline configuration on segment interfaces."""
        record = self.active_faults.get(segment_name)
        if record is None:
            return False

        orig = record.original_config
        link = self.find_segment_link(segment_name)
        if link is not None:
            intf1, intf2 = link.intf1, link.intf2
            errors = []
            for intf in (intf1, intf2):
                try:
                    self._configure(intf, orig)
                except Exception as exc:
                    errors.append(f'{intf.name}: {exc}')
            if errors:
                raise RuntimeError('Restoration failed: ' + '; '.join(errors))

        del self.active_faults[segment_name]
        return True

    @staticmethod
    def _configure(intf, params):
        """TCIntf returns command output even when tc fails: inspect every result."""
        result = intf.config(bw=params['bw'], delay=params['delay'], loss=params['loss'],
                             max_queue_size=params.get('max_queue_size', 1000))
        if not isinstance(result, dict):
            raise RuntimeError(f'tc configuration failed on {intf.name}: {result}')
        # HTB emits this advisory on successful high-bandwidth class creation.
        # Other warnings and errors, including mixed output blocks, still fail.
        advisory = re.compile(r'Warning: sch_htb: quantum of class [0-9a-fA-F]+ is big\. Consider r2q change\.')
        lines = [line.strip() for out in result.get('tcoutputs', [])
                 for line in str(out).splitlines() if line.strip()]
        if any(not advisory.fullmatch(line) for line in lines):
            raise RuntimeError(f'tc configuration failed on {intf.name}: {result}')
        output = intf.cmd('tc qdisc show dev', intf.name)
        if 'netem' not in output or 'htb' not in output:
            raise RuntimeError(f'Expected HTB/netem qdisc missing on {intf.name}: {output}')
        if lines:
            # A warning is acceptable only after exact parameter readback succeeds.
            classes = intf.cmd('tc class show dev', intf.name)
            actual = FaultInjector.parse_state(output, classes)
            FaultInjector._assert_parameters(intf.name, actual, params)

    def restore_all(self):
        """Restore all active impairments."""
        errors = []
        for seg in list(self.active_faults.keys()):
            try:
                self.restore_fault(seg)
            except Exception as exc:
                errors.append(f'{seg}: {exc}')
        if errors:
            raise RuntimeError('Failed to restore some faults: ' + '; '.join(errors))

    def snapshot_segment(self, segment_name):
        """Capture actual kernel configuration on both owned segment interfaces."""
        link = self.find_segment_link(segment_name)
        if link is None:
            return {'mode': 'simulation', 'interfaces': []}
        interfaces = []
        for intf in (link.intf1, link.intf2):
            qdisc = intf.cmd('tc qdisc show dev', intf.name)
            classes = intf.cmd('tc class show dev', intf.name)
            interfaces.append({'interface': intf.name, 'qdisc': qdisc, 'classes': classes,
                               'parameters': self.parse_state(qdisc, classes)})
        return {'mode': 'live_kernel', 'interfaces': interfaces}

    @staticmethod
    def parse_state(qdisc, classes):
        """Parse tc's configuration fields, ignoring counters and timing noise."""
        if 'qdisc htb ' not in qdisc or 'qdisc netem ' not in qdisc:
            raise RuntimeError('Owned segment is missing its expected HTB/netem hierarchy')
        delay = re.search(r'\bdelay\s+([\d.]+)(us|ms|s)\b', qdisc)
        loss = re.search(r'\bloss\s+(?:random\s+)?([\d.]+)%', qdisc)
        rate = re.search(r'\brate\s+([\d.]+)([KMG]?)bit\b', classes, re.IGNORECASE)
        limit = re.search(r'\blimit\s+(\d+)\b', qdisc)
        if rate is None or limit is None:
            raise RuntimeError('Unable to verify owned segment bandwidth or queue limit')
        return {
            'delay_ms': float(delay[1]) * {'us': 0.001, 'ms': 1, 's': 1000}[delay[2]] if delay else 0.0,
            'loss': float(loss[1]) if loss else 0.0,
            'bw': float(rate[1]) * {'': 0.000001, 'K': 0.001, 'M': 1, 'G': 1000}[rate[2].upper()],
            'max_queue_size': int(limit[1]),
        }

    def verify_segment(self, segment_name, expected=None):
        """Verify actual delay, loss, rate and queue against the requested config."""
        snapshot = self.snapshot_segment(segment_name)
        if self.net is None:
            return snapshot
        config = expected or SEGMENTS[segment_name]
        for interface in snapshot['interfaces']:
            self._assert_parameters(interface['interface'], interface['parameters'], config)
        return snapshot

    @staticmethod
    def _assert_parameters(interface_name, actual, config):
        match = re.fullmatch(r'([\d.]+)(us|ms|s)', config['delay'])
        wanted = {'delay_ms': float(match[1]) * {'us': 0.001, 'ms': 1, 's': 1000}[match[2]],
                  'loss': config['loss'], 'bw': config['bw'],
                  'max_queue_size': config.get('max_queue_size', 1000)}
        for key, value in wanted.items():
            if not math.isclose(actual[key], value, rel_tol=0.001, abs_tol=0.001):
                raise RuntimeError(f'Actual {key} on {interface_name} does not match '
                                   f'requested value {value}: {actual[key]}')


@contextmanager
def inject_fault(net, segment_name: str,
                 delay: Optional[str] = None,
                 loss: Optional[float] = None,
                 bw: Optional[float] = None):
    """
    Context manager that attempts restoration and reports cleanup failures.
    """
    injector = FaultInjector(net)
    try:
        injector.apply_fault(segment_name, delay=delay, loss=loss, bw=bw)
        yield injector
    finally:
        injector.restore_all()
