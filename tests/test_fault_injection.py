"""Unit tests for FaultInjector lifecycle and safety guarantees."""

import unittest
from src.traffic.fault_injection import FaultInjector, inject_fault
from src.topology.cloud_topology import SEGMENTS


class TestFaultInjector(unittest.TestCase):
    def setUp(self):
        # Pass net=None to test logic, state tracking, and parameter preservation
        self.injector = FaultInjector(net=None)

    def test_unknown_segment_rejected(self):
        with self.assertRaises(ValueError):
            self.injector.apply_fault('invalid_segment_xyz', delay='50ms')

    def test_apply_and_restore_cycle(self):
        target = 'seg_client_edge'
        rec = self.injector.apply_fault(target, delay='50ms', loss=5.0)

        self.assertEqual(rec.segment, target)
        self.assertEqual(rec.fault_type, 'latency+loss')
        self.assertEqual(rec.parameters['delay'], '50ms')
        self.assertEqual(rec.parameters['loss'], 5.0)
        self.assertEqual(rec.original_config, SEGMENTS[target])
        self.assertIn(target, self.injector.active_faults)

        # Restoring clears active state
        self.assertTrue(self.injector.restore_fault(target))
        self.assertNotIn(target, self.injector.active_faults)

    def test_duplicate_active_fault_rejected(self):
        target = 'seg_edge_lb'
        self.injector.apply_fault(target, delay='30ms')
        with self.assertRaises(RuntimeError):
            self.injector.apply_fault(target, delay='60ms')
        self.injector.restore_fault(target)

    def test_context_manager_cleans_up_on_exception(self):
        target = 'seg_app_db'
        with self.assertRaises(ZeroDivisionError):
            with inject_fault(net=None, segment_name=target, loss=20.0) as inj:
                self.assertIn(target, inj.active_faults)
                _ = 1 / 0

        # After exception, active fault must be cleaned up
        self.assertEqual(len(inj.active_faults), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
