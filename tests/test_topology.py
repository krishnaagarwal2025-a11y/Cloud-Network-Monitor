#!/usr/bin/env python3
"""
Unit and Integration Tests for Cloud Application Network Topology
-----------------------------------------------------------------
Verifies:
1. All expected hosts and switches are created.
2. All intended links exist with correct attributes.
3. Hosts can communicate where expected (when run as root).
4. The topology can start and cleanly shut down (when run as root).
"""

import os
import sys
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.topology.cloud_topology import (
    CloudTopology,
    create_network,
    HOST_CONFIGS,
    SWITCH_CONFIGS,
    HOST_ATTACHMENTS,
    SEGMENTS,
)


class TestCloudTopologyStructure(unittest.TestCase):
    """Verifies the structural correctness of the CloudTopology graph."""

    def setUp(self):
        self.topo = CloudTopology()

    def test_expected_hosts_exist(self):
        """1. Verify all 7 expected hosts exist."""
        topo_hosts = set(self.topo.hosts())
        expected_hosts = set(HOST_CONFIGS.keys())
        self.assertEqual(
            topo_hosts,
            expected_hosts,
            f"Host mismatch. Found: {topo_hosts}, Expected: {expected_hosts}",
        )

    def test_expected_switches_exist(self):
        """1b. Verify all 5 expected tier switches exist."""
        topo_switches = set(self.topo.switches())
        expected_switches = set(SWITCH_CONFIGS.keys())
        self.assertEqual(
            topo_switches,
            expected_switches,
            f"Switch mismatch. Found: {topo_switches}, Expected: {expected_switches}",
        )

    def test_host_to_switch_links_exist(self):
        """2a. Verify all hosts are attached to their designated tier switches."""
        links = self.topo.links()
        link_pairs = {tuple(sorted([src, dst])) for src, dst in links}

        for host, switch in HOST_ATTACHMENTS.items():
            pair = tuple(sorted([host, switch]))
            self.assertIn(
                pair,
                link_pairs,
                f"Missing host-to-switch link between {host} and {switch}",
            )

    def test_segment_links_exist(self):
        """2b. Verify all 4 measurable tier segment links exist."""
        links = self.topo.links()
        link_pairs = {tuple(sorted([src, dst])) for src, dst in links}

        for seg_name, seg_conf in SEGMENTS.items():
            pair = tuple(sorted([seg_conf['node1'], seg_conf['node2']]))
            self.assertIn(
                pair,
                link_pairs,
                f"Missing segment link for {seg_name} between {pair[0]} and {pair[1]}",
            )

    def test_total_link_count(self):
        """2c. Verify total link count (7 host links + 4 segment links = 11 links)."""
        expected_count = len(HOST_ATTACHMENTS) + len(SEGMENTS)
        self.assertEqual(len(self.topo.links()), expected_count)

    def test_deterministic_host_addressing(self):
        """Verify deterministic IP configuration for each host."""
        for host_name, expected_conf in HOST_CONFIGS.items():
            node_info = self.topo.nodeInfo(host_name)
            self.assertEqual(
                node_info.get('ip'),
                expected_conf['ip'],
                f"IP mismatch for {host_name}: {node_info.get('ip')} != {expected_conf['ip']}",
            )

    def test_deterministic_switch_dpids(self):
        """Verify explicit deterministic DPID assignment for all tier switches."""
        for switch_name, expected_conf in SWITCH_CONFIGS.items():
            node_info = self.topo.nodeInfo(switch_name)
            self.assertEqual(
                node_info.get('dpid'),
                expected_conf['dpid'],
                f"DPID mismatch for {switch_name}: {node_info.get('dpid')} != {expected_conf['dpid']}",
            )


class TestCloudTopologyLiveNetwork(unittest.TestCase):
    """
    Integration tests running actual Mininet network namespaces.
    Requires root (sudo) privileges.
    """

    @unittest.skipUnless(os.geteuid() == 0, "Live Mininet test requires root (sudo) privileges.")
    def test_live_network_lifecycle_and_connectivity(self):
        """
        Tests:
        3. Hosts can communicate where expected.
        4. The topology can start and cleanly shut down.
        """
        net = None
        try:
            print("\n*** Starting live Mininet testbed (controllerless OVSBridge mode)...")
            net = create_network()
            net.start()

            # Verify no external OpenFlow controller is configured or required
            self.assertEqual(
                len(net.controllers),
                0,
                f"Expected 0 external controllers, found {len(net.controllers)}",
            )

            # Verify switches exist with exact deterministic DPIDs
            for switch_name, s_conf in SWITCH_CONFIGS.items():
                sw = net.get(switch_name)
                self.assertIsNotNone(sw, f"Live switch {switch_name} not found in Mininet")
                expected_dpid = s_conf['dpid']
                self.assertEqual(
                    sw.dpid,
                    expected_dpid,
                    f"Live DPID mismatch on {switch_name}: {sw.dpid} != {expected_dpid}",
                )

            # Verify hosts exist in live network
            for host_name in HOST_CONFIGS:
                host = net.get(host_name)
                self.assertIsNotNone(host, f"Live host {host_name} not found in Mininet")
                expected_ip = HOST_CONFIGS[host_name]['ip'].split('/')[0]
                self.assertEqual(
                    host.IP(),
                    expected_ip,
                    f"Live IP mismatch on {host_name}: {host.IP()} != {expected_ip}",
                )

            # Test targeted tier-to-tier connectivity
            print("*** Testing tier-to-tier connectivity...")
            client = net.get('client')
            edge_gw = net.get('edge_gw')
            lb = net.get('lb')
            web_srv = net.get('web_srv')
            api_srv = net.get('api_srv')
            auth_srv = net.get('auth_srv')
            db_srv = net.get('db_srv')

            # Client -> Edge Gateway
            loss = net.ping(hosts=[client, edge_gw], timeout='1')
            self.assertEqual(loss, 0.0, "Ping failed: client <-> edge_gw")

            # Edge Gateway -> Load Balancer
            loss = net.ping(hosts=[edge_gw, lb], timeout='1')
            self.assertEqual(loss, 0.0, "Ping failed: edge_gw <-> lb")

            # Load Balancer -> App Services
            for app in [web_srv, api_srv, auth_srv]:
                loss = net.ping(hosts=[lb, app], timeout='1')
                self.assertEqual(loss, 0.0, f"Ping failed: lb <-> {app.name}")

            # App Services -> Database
            for app in [web_srv, api_srv, auth_srv]:
                loss = net.ping(hosts=[app, db_srv], timeout='1')
                self.assertEqual(loss, 0.0, f"Ping failed: {app.name} <-> db_srv")

            # Specific end-to-end ping verifications (3 packets each)
            print("*** Testing client ping -c 3 edge_gw...")
            out = client.cmd(f'ping -c 3 {edge_gw.IP()}')
            self.assertIn(' 0% packet loss', out, f"Packet loss during client -> edge_gw: {out}")

            print("*** Testing web_srv ping -c 3 db_srv...")
            out = web_srv.cmd(f'ping -c 3 {db_srv.IP()}')
            self.assertIn(' 0% packet loss', out, f"Packet loss during web_srv -> db_srv: {out}")

            print("*** Testing client ping -c 3 db_srv (end-to-end)...")
            out = client.cmd(f'ping -c 3 {db_srv.IP()}')
            self.assertIn(' 0% packet loss', out, f"Packet loss during client -> db_srv: {out}")

            # Test full network pingall (0% packet loss)
            print("*** Testing pingAll across all nodes...")
            total_loss = net.pingAll(timeout='1')
            self.assertEqual(
                total_loss,
                0.0,
                f"pingAll had packet loss: {total_loss}%",
            )
            print("*** All connectivity checks passed with 0% loss!")

        finally:
            if net is not None:
                print("*** Cleanly stopping Mininet network...")
                net.stop()
                print("*** Network stopped successfully.")


def run_tests():
    """Run test suite."""
    suite = unittest.TestSuite()
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestCloudTopologyStructure))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestCloudTopologyLiveNetwork))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if os.geteuid() != 0:
        print("\n" + "=" * 60)
        print("NOTE: Structural verification tests PASSED.")
        print("To run the live Mininet communication and lifecycle tests,")
        print("execute with sudo:")
        print("    sudo python3 tests/test_topology.py")
        print("=" * 60 + "\n")
        
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
