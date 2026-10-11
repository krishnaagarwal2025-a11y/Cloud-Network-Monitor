"""Safe experiment lifecycle around the existing, unchanged topology."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile

from mininet.net import Mininet
from mininet.node import OVSBridge
from mininet.link import TCLink
from src.topology.cloud_topology import CloudTopology, SWITCH_CONFIGS, HOST_CONFIGS


def preflight():
    """Refuse collisions instead of deleting stale or unrelated resources."""
    result = subprocess.run(['ovs-vsctl', '--timeout=3', 'list-br'],
                            capture_output=True, text=True, check=True)
    collisions = set(result.stdout.split()) & set(SWITCH_CONFIGS)
    interfaces = {p.name for p in Path('/sys/class/net').iterdir()}
    names = set(HOST_CONFIGS) | set(SWITCH_CONFIGS)
    collisions |= {i for i in interfaces if any(i.startswith(n + '-eth') for n in names)}
    if collisions:
        raise RuntimeError('Existing project-name network resources; inspect before retrying: '
                           + ', '.join(sorted(collisions)))


@contextmanager
def experiment_network(state=None):
    """Lock concurrent runs, preflight, and retain ownership during partial build."""
    state = state if state is not None else {}
    state['cleanup_success'] = False
    if os.geteuid() != 0:
        raise PermissionError('Live Mininet experiments require sudo')
    lock_path = Path(tempfile.gettempdir()) / 'cloud-network-monitor.lock'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        preflight()
        net = Mininet(topo=CloudTopology(), switch=OVSBridge, controller=None,
                      link=TCLink, autoSetMacs=True, autoStaticArp=True, build=False)
        try:
            net.build()
            net.start()
            yield net
        finally:
            # Mininet normally signals node shells but does not always reap them.
            shells = [node.shell for node in net.hosts + net.switches if node.shell]
            try:
                net.stop()
            finally:
                for shell in shells:
                    try:
                        shell.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        shell.kill()
                        shell.wait(timeout=2)
            preflight()
            state['cleanup_success'] = True
