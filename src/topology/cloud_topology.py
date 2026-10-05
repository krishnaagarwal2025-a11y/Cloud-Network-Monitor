#!/usr/bin/env python3
"""
Cloud Application Network Monitoring - Multi-Tier Cloud Topology
---------------------------------------------------------------
Logical Segments:
    Client (client)
      ↓ [seg_client_edge: 100Mbps, 5ms]
    Edge / Internet Gateway (edge_gw)
      ↓ [seg_edge_lb: 1Gbps, 2ms]
    Load Balancer (lb)
      ↓ [seg_lb_app: 1Gbps, 1ms]
    Application / Service Layer
      ├── Web service (web_srv)
      ├── API service (api_srv)
      └── Auth service (auth_srv)
      ↓ [seg_app_db: 1Gbps, 1ms]
    Database (db_srv)

This topology creates separate measurable network segments using dedicated
switches and TCLinks so that network metrics (latency, jitter, loss, throughput)
can be measured, faults injected via tc/netem, and anomalies localized.
"""

from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import OVSBridge
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info


# Host configurations with deterministic IP and MAC addresses
HOST_CONFIGS = {
    'client': {
        'ip': '10.0.0.10/24',
        'mac': '00:00:00:00:00:10',
        'description': 'Client simulating external user requests',
    },
    'edge_gw': {
        'ip': '10.0.0.1/24',
        'mac': '00:00:00:00:00:01',
        'description': 'Edge / Ingress Internet Gateway',
    },
    'lb': {
        'ip': '10.0.0.2/24',
        'mac': '00:00:00:00:00:02',
        'description': 'Internal Load Balancer',
    },
    'web_srv': {
        'ip': '10.0.0.11/24',
        'mac': '00:00:00:00:00:11',
        'description': 'Frontend Web Application Service',
    },
    'api_srv': {
        'ip': '10.0.0.12/24',
        'mac': '00:00:00:00:00:12',
        'description': 'Backend API Service',
    },
    'auth_srv': {
        'ip': '10.0.0.13/24',
        'mac': '00:00:00:00:00:13',
        'description': 'Authentication / Identity Service',
    },
    'db_srv': {
        'ip': '10.0.0.20/24',
        'mac': '00:00:00:00:00:20',
        'description': 'Database Storage Backend Service',
    },
}

# Switch configurations representing tier boundaries with deterministic Datapath IDs (DPIDs)
SWITCH_CONFIGS = {
    's_client': {
        'dpid': '0000000000000001',
        'description': 'Client Access Tier Switch',
    },
    's_edge': {
        'dpid': '0000000000000002',
        'description': 'Edge Gateway Tier Switch',
    },
    's_lb': {
        'dpid': '0000000000000003',
        'description': 'Load Balancer DMZ Switch',
    },
    's_app': {
        'dpid': '0000000000000004',
        'description': 'Application Services Mesh Switch',
    },
    's_db': {
        'dpid': '0000000000000005',
        'description': 'Database Tier Switch',
    },
}

# Host to switch attachments
HOST_ATTACHMENTS = {
    'client': 's_client',
    'edge_gw': 's_edge',
    'lb': 's_lb',
    'web_srv': 's_app',
    'api_srv': 's_app',
    'auth_srv': 's_app',
    'db_srv': 's_db',
}

# Measurable inter-tier network segments with baseline QoS parameters
SEGMENTS = {
    'seg_client_edge': {
        'node1': 's_client',
        'node2': 's_edge',
        'bw': 100,       # 100 Mbps WAN link
        'delay': '5ms',  # 5ms WAN latency
        'loss': 0,       # 0% baseline loss
        'max_queue_size': 1000,
        'description': 'Client to Edge Gateway WAN segment',
    },
    'seg_edge_lb': {
        'node1': 's_edge',
        'node2': 's_lb',
        'bw': 1000,      # 1 Gbps internal link
        'delay': '2ms',  # 2ms perimeter latency
        'loss': 0,
        'max_queue_size': 1000,
        'description': 'Edge Gateway to Load Balancer DMZ segment',
    },
    'seg_lb_app': {
        'node1': 's_lb',
        'node2': 's_app',
        'bw': 1000,      # 1 Gbps service link
        'delay': '1ms',  # 1ms cluster latency
        'loss': 0,
        'max_queue_size': 1000,
        'description': 'Load Balancer to App Services mesh segment',
    },
    'seg_app_db': {
        'node1': 's_app',
        'node2': 's_db',
        'bw': 1000,      # 1 Gbps backend link
        'delay': '1ms',  # 1ms backend latency
        'loss': 0,
        'max_queue_size': 1000,
        'description': 'App Services to Database backend segment',
    },
}


class CloudTopology(Topo):
    """
    Multi-tier Cloud Application Network Topology for Mininet.
    """

    def build(self, **kwargs):
        """Construct switches, hosts, and interconnected segment links."""
        # 1. Create Tier Switches with deterministic DPIDs using OVSBridge (no external controller)
        switches = {}
        for s_name, s_conf in SWITCH_CONFIGS.items():
            switches[s_name] = self.addSwitch(
                s_name,
                cls=OVSBridge,
                dpid=s_conf['dpid'],
            )

        # 2. Create Hosts with deterministic IP and MAC addresses
        hosts = {}
        for h_name, h_conf in HOST_CONFIGS.items():
            hosts[h_name] = self.addHost(
                h_name,
                ip=h_conf['ip'],
                mac=h_conf['mac'],
            )

        # 3. Connect Hosts to their respective Tier Switch
        for h_name, s_name in HOST_ATTACHMENTS.items():
            # Fast low-latency link from host to tier switch
            self.addLink(
                h_name,
                s_name,
                bw=1000,
                delay='0.1ms',
                loss=0,
            )

        # 4. Connect Tier Switches along the measurable segments
        for seg_name, seg_conf in SEGMENTS.items():
            self.addLink(
                seg_conf['node1'],
                seg_conf['node2'],
                bw=seg_conf['bw'],
                delay=seg_conf['delay'],
                loss=seg_conf['loss'],
                max_queue_size=seg_conf.get('max_queue_size', 1000),
            )


def create_network(topo=None, interactive=False):
    """
    Instantiate and configure the Mininet network without an external controller.
    Uses OVSBridge (OVSSwitch in standalone bridge mode) for reliable kernel-level
    L2 learning switch forwarding without requiring an OpenFlow controller executable.
    """
    if topo is None:
        topo = CloudTopology()

    net = Mininet(
        topo=topo,
        switch=OVSBridge,
        controller=None,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=True,
    )
    return net


# Mininet custom topology dictionary for `mn --custom`
topos = {'cloud': (lambda: CloudTopology())}


def run_interactive():
    """Start topology and drop into Mininet interactive CLI."""
    setLogLevel('info')
    info('*** Building Cloud Application Network Topology\n')
    net = create_network(interactive=True)
    
    info('*** Starting Network\n')
    net.start()
    
    info('\n*** Topology Nodes Summary:\n')
    for h in net.hosts:
        info(f'  Host: {h.name:<10} IP: {h.IP():<15} MAC: {h.MAC()}\n')
    for s in net.switches:
        info(f'  Switch: {s.name:<10}\n')
        
    info('\n*** Entering Mininet CLI. Type "exit" or "quit" to stop.\n')
    CLI(net)
    
    info('*** Stopping Network\n')
    net.stop()


if __name__ == '__main__':
    run_interactive()
