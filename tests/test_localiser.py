"""Unit tests for Boolean Network Tomography and Segment Fault Localisation."""

import unittest
from src.analysis.localiser import FaultLocaliser, LocalisationResult


def make_topology_report(impaired_path=None, impaired_latency=60.0, impaired_loss=10.0):
    paths = [
        ('client', 'edge_gw', 10.0, 0.0),
        ('client', 'web_srv', 16.0, 0.0),
        ('client', 'api_srv', 16.0, 0.0),
        ('client', 'db_srv', 18.0, 0.0),
        ('web_srv', 'db_srv', 2.0, 0.0),
    ]

    icmp = []
    for src, tgt, lat, loss in paths:
        path_name = f'{src}->{tgt}'
        if impaired_path and (path_name in impaired_path or path_name == impaired_path):
            lat = impaired_latency
            loss = impaired_loss
        icmp.append({
            'source': src,
            'target': tgt,
            'success': (loss < 100.0),
            'rtt_avg_ms': lat,
            'packet_loss_percent': loss,
            'timestamp': 1000.0,
        })

    return {'timestamp': 1000.0, 'success': True, 'icmp': icmp}


class TestFaultLocaliser(unittest.TestCase):
    def setUp(self):
        self.localiser = FaultLocaliser(latency_spike_threshold_ms=15.0, loss_threshold_percent=2.0)

    def test_healthy_network_localises_no_fault(self):
        report = make_topology_report()
        res = self.localiser.localise(report)
        self.assertEqual(res.status, 'healthy')
        self.assertEqual(len(res.candidate_segments), 0)
        self.assertEqual(res.confidence, 1.0)
        eval_gt = res.evaluate_ground_truth(None)
        self.assertEqual(eval_gt['classification'], 'correct_clean')

    def test_client_edge_fault_localised_unambiguously(self):
        # A fault on seg_client_edge impairs ALL client paths, but web_srv->db_srv is healthy
        bad_paths = ['client->edge_gw', 'client->web_srv', 'client->api_srv', 'client->db_srv']
        report = make_topology_report(impaired_path=bad_paths, impaired_latency=70.0)
        res = self.localiser.localise(report)

        self.assertEqual(res.status, 'localised')
        self.assertFalse(res.is_ambiguous)
        self.assertEqual(res.candidate_segments, ['seg_client_edge'])
        self.assertEqual(res.confidence, 1.0)

        eval_gt = res.evaluate_ground_truth('seg_client_edge')
        self.assertEqual(eval_gt['classification'], 'exact_match')
        self.assertTrue(eval_gt['matched'])

    def test_app_db_fault_localised_unambiguously(self):
        # A fault on seg_app_db impairs client->db_srv and web_srv->db_srv
        bad_paths = ['client->db_srv', 'web_srv->db_srv']
        report = make_topology_report(impaired_path=bad_paths, impaired_loss=25.0)
        res = self.localiser.localise(report)

        self.assertEqual(res.status, 'localised')
        self.assertFalse(res.is_ambiguous)
        self.assertEqual(res.candidate_segments, ['seg_app_db'])
        self.assertEqual(res.confidence, 1.0)

        eval_gt = res.evaluate_ground_truth('seg_app_db')
        self.assertEqual(eval_gt['classification'], 'exact_match')

    def test_intermediate_core_fault_reports_ambiguity_honestly(self):
        # A fault on seg_edge_lb impairs client->web_srv, api_srv, db_srv,
        # but client->edge_gw is clean and web_srv->db_srv is clean.
        bad_paths = ['client->web_srv', 'client->api_srv', 'client->db_srv']
        report = make_topology_report(impaired_path=bad_paths, impaired_latency=60.0)
        res = self.localiser.localise(report)

        self.assertEqual(res.status, 'ambiguous')
        self.assertTrue(res.is_ambiguous)
        self.assertEqual(res.candidate_segments, ['seg_edge_lb', 'seg_lb_app'])
        self.assertEqual(res.confidence, 0.5)

        # Ground truth evaluation recognizes the target is in the candidate set
        eval_gt = res.evaluate_ground_truth('seg_edge_lb')
        self.assertEqual(eval_gt['classification'], 'ambiguous_contains_target')
        self.assertTrue(eval_gt['matched'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
