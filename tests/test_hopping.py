"""跳频时间戳/载频一致性、有限时隙和迟到停止策略，无射频操作。"""
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flrc_modem.config import DemodConfig
from flrc_modem.hopping import HopBurst, HoppingPlan, prepare_hopping_plan
from flrc_modem.io.iq import decode_samples
from flrc_modem.modulation import ModulationConfig
from flrc_modem.parsing.api import load_parser
from flrc_modem.pipeline import WindowDecoder
from flrc_modem.sdr.hackrf import StreamCancelled


RADIO = {"sync_word": {"ota_sync_word_hex": "ecee6698"},
         "hopping_table": [{"frequency_hz": 2400060000 + i * 400000} for i in range(20)]}
MESSAGE = {"direction": "ground_to_air", "command": "0x11",
           "fields": {"channels_raw": [1024] * 16, "data_batch_id": 0}}


class HoppingTests(unittest.TestCase):
    def test_slot_timestamp_and_actual_generated_packets_match_table(self):
        modulation = ModulationConfig(sample_rate=2000000)
        plan = prepare_hopping_plan(MESSAGE, RADIO, modulation, cycles=1)
        self.assertEqual(len(plan.bursts), 20)
        self.assertEqual(plan.packet_count, 60)
        self.assertEqual(len(plan.sent_packets), 60)
        config = DemodConfig(sample_rate=2000000)
        parser = load_parser("dk32s")
        for slot in (0, 17, 19):
            burst = plan.bursts[slot]
            frames = list(WindowDecoder(config).decode(decode_samples(burst.iq, "cs8"), 0))
            self.assertEqual(len(frames), 3)
            for frame in frames:
                app = parser(frame)
                self.assertEqual((app["timestamp_ticks"] % 10000) // 500, slot)
                self.assertIn((frame.packet.sync_word.hex(), frame.packet.payload.hex()), plan.sent_packets)
            self.assertEqual(burst.frequency_hz, RADIO["hopping_table"][slot]["frequency_hz"])
        self.assertNotIn("timestamp_ticks", MESSAGE)

    def test_invalid_timing_and_long_packets_are_rejected_before_rf(self):
        modulation = ModulationConfig(sample_rate=2000000)
        for options in ({"cycles": 0}, {"dwell_seconds": .012},
                        {"packets_per_slot": 0}, {"packets_per_slot": 10},
                        {"packet_interval": .0001}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                prepare_hopping_plan(MESSAGE, RADIO, modulation, **options)

    def test_wrong_direction_and_bad_radio_config_are_rejected(self):
        with self.assertRaises(ValueError):
            prepare_hopping_plan({"direction": "air_to_ground"}, RADIO, ModulationConfig())
        with self.assertRaises(ValueError):
            prepare_hopping_plan(MESSAGE, {**RADIO, "hopping_table": []}, ModulationConfig())

    def test_missed_slot_is_not_transmitted_with_stale_timestamp(self):
        plan = HoppingPlan((HopBurst(0, 0, 2480060000, b"\0" * 4000),), frozenset(), 2000000, 60000, .05, 1)
        device = SimpleNamespace(transmit=Mock())
        with patch("flrc_modem.hopping.time.monotonic", side_effect=[0, .2, .2]):
            result = plan.transmit(device, threading.Event())
        self.assertEqual(result["skipped_slots"], 1)
        self.assertEqual(result["slots"][0]["status"], "skipped_late")
        device.transmit.assert_not_called()

    def test_cancelled_plan_never_starts_rf(self):
        plan = HoppingPlan((HopBurst(0, 0, 2480060000, b"\0" * 4000),), frozenset(), 2000000, 60000, .05, 1)
        cancel = threading.Event()
        cancel.set()
        device = SimpleNamespace(transmit=Mock())
        with self.assertRaises(StreamCancelled):
            plan.transmit(device, cancel)
        device.transmit.assert_not_called()

    def test_unbounded_options_are_rejected_before_numpy_allocation(self):
        with patch("flrc_modem.hopping.np.zeros", side_effect=AssertionError("不应分配波形")):
            for options in ({"cycles": 10**9}, {"packets_per_slot": 10**9}, {"guard_seconds": 10**9}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    prepare_hopping_plan(MESSAGE, RADIO, ModulationConfig(sample_rate=2000000), **options)

    def test_failed_slot_retains_frequency_error_and_elapsed_time(self):
        plan = HoppingPlan((HopBurst(7, 0, 2480060000, b"\0" * 4000),), frozenset(), 2000000, 60000, .05, 1)
        device = SimpleNamespace(transmit=Mock(side_effect=OSError("USB failure")), last_stats=None)
        slots = []
        with self.assertRaisesRegex(OSError, "USB failure"):
            plan.transmit(device, threading.Event(), slot_stats=slots)
        self.assertEqual(slots[0]["slot"], 7)
        self.assertEqual(slots[0]["frequency_hz"], 2480060000)
        self.assertEqual(slots[0]["status"], "failed")
        self.assertIn("USB failure", slots[0]["error"])


if __name__ == "__main__":
    unittest.main()
