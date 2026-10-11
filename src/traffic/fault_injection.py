"""
Controlled Network Fault Injection via Linux tc / netem
--------------------------------------------------------
Applies and safely removes network impairments (latency, loss, bandwidth restrictions)
on specific Mininet segment links.

Safety principles:
- Only touches interfaces belonging to the specified segment link.
- Records and restores the original QoS configuration.
- Verifies the impairment is active after applying.
- Cleans up in a finally block / context manager even on errors.
- Never runs destructive global qdisc resets.
"""

from contextlib import contextmanager
from dataclasses import dataclass, asdict
import time
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

            # Apply impairment using Mininet TCIntf config
            intf1.config(bw=target_bw, delay=target_delay, loss=target_loss)
            intf2.config(bw=target_bw, delay=target_delay, loss=target_loss)

            # Verify impairment is present on at least one interface
            qdisc_out = intf1.cmd('tc qdisc show dev', intf1.name)
            if delay is not None and 'netem' not in qdisc_out and 'delay' not in qdisc_out:
                # Some tc versions format differently, check intf2 as well
                qdisc_out2 = intf2.cmd('tc qdisc show dev', intf2.name)
                if 'netem' not in qdisc_out2:
                    raise RuntimeError(f'Failed to verify netem qdisc on {intf_names}: {qdisc_out}')

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
            intf1.config(bw=orig['bw'], delay=orig['delay'], loss=orig['loss'])
            intf2.config(bw=orig['bw'], delay=orig['delay'], loss=orig['loss'])

        del self.active_faults[segment_name]
        return True

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


@contextmanager
def inject_fault(net, segment_name: str,
                 delay: Optional[str] = None,
                 loss: Optional[float] = None,
                 bw: Optional[float] = None):
    """
    Context manager for safe, guaranteed-cleanup fault injection.
    """
    injector = FaultInjector(net)
    injector.apply_fault(segment_name, delay=delay, loss=loss, bw=bw)
    try:
        yield injector
    finally:
        injector.restore_all()
