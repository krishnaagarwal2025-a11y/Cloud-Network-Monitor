"""Unit tests for FaultInjector lifecycle and safety guarantees."""

import unittest
from unittest.mock import Mock
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


class TestHTBAdvisory(unittest.TestCase):
    WARNING = 'Warning: sch_htb: quantum of class 50001 is big. Consider r2q change.\r\n'
    PARAMS = {'bw': 100, 'delay': '50ms', 'loss': 0}

    def interface(self, outputs=None, delay='50ms'):
        intf = Mock()
        intf.name = 's_client-eth2'
        intf.config.return_value = {'tcoutputs': outputs if outputs is not None else ['', self.WARNING, '']}
        intf.cmd.side_effect = lambda command, name: (
            f'qdisc htb 5: root\nqdisc netem 10: parent 5:1 limit 1000 delay {delay}'
            if 'qdisc' in command else 'class htb 5:1 root rate 100Mbit ceil 100Mbit')
        return intf

    def test_known_advisory_requires_successful_parameter_readback(self):
        intf = self.interface()
        FaultInjector._configure(intf, self.PARAMS)
        self.assertEqual(intf.cmd.call_count, 2)
        self.assertIn('class show', intf.cmd.call_args.args[0])

    def test_real_error_alongside_advisory_still_fails(self):
        intf = self.interface([self.WARNING + 'RTNETLINK answers: Invalid argument\n'])
        with self.assertRaisesRegex(RuntimeError, 'tc configuration failed'):
            FaultInjector._configure(intf, self.PARAMS)
        intf.cmd.assert_not_called()

    def test_other_warning_still_fails(self):
        intf = self.interface(['Warning: unrecognized configuration problem'])
        with self.assertRaisesRegex(RuntimeError, 'tc configuration failed'):
            FaultInjector._configure(intf, self.PARAMS)

    def test_advisory_cannot_hide_stale_parameters(self):
        for field, changed in (('delay', {'delay': '5ms'}), ('bw', {'bw': 20}), ('loss', {'loss': 100})):
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'does not match'):
                # The readback stays at 100 Mbps / 50 ms / zero loss.
                FaultInjector._configure(self.interface(), {**self.PARAMS, **changed})

    def test_advisory_cannot_hide_missing_qdisc(self):
        intf = self.interface()
        intf.cmd.side_effect = None
        intf.cmd.return_value = 'qdisc noqueue root'
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            FaultInjector._configure(intf, self.PARAMS)

    def test_apply_and_restore_with_advisories_keep_lifecycle_correct(self):
        interfaces = []
        for name in ('s_client-eth2', 's_edge-eth2'):
            intf = self.interface()
            intf.name = name
            state = {}
            def config(_state=state, **params):
                _state.update(params)
                return {'tcoutputs': [self.WARNING]}
            def cmd(command, name, _state=state):
                if 'qdisc' in command:
                    return f'qdisc htb 5: root\nqdisc netem 10: parent 5:1 limit 1000 delay {_state["delay"]}'
                return f'class htb 5:1 root rate {_state["bw"]}Mbit'
            intf.config.side_effect = config
            intf.cmd.side_effect = cmd
            interfaces.append(intf)
        injector = FaultInjector(Mock())
        injector.find_segment_link = Mock(return_value=Mock(intf1=interfaces[0], intf2=interfaces[1]))
        injector.apply_fault('seg_client_edge', delay='50ms')
        self.assertIn('seg_client_edge', injector.active_faults)
        self.assertTrue(injector.restore_fault('seg_client_edge'))
        self.assertEqual(injector.active_faults, {})
        for intf in interfaces:
            self.assertEqual(intf.config.call_count, 2)
            self.assertEqual(intf.config.call_args.kwargs['delay'], '5ms')


if __name__ == '__main__':
    unittest.main(verbosity=2)
