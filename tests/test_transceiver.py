"""单元测试：基于硬件时间戳的交互式 TDD 双工收发机 (LiveTDDTransceiver)。"""
import io
import json
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from flrc_modem.models import DecodedFrame
from flrc_modem.phy.packet import PhysicalPacket
from flrc_modem.sdr.usrp import USRPConfig, USRPDevice
from flrc_modem.sync import (
    LiveTDDTransceiver,
    SyncSnapshot,
    SyncState,
    TDDExchangeResult,
)
from flrc_modem.duplex_cli import make_argument_parser, main as duplex_cli_main
from usrp_fake import FakeUHDLibrary


class TransceiverTests(unittest.TestCase):
    def test_duplex_cli_argument_parser(self):
        parser = make_argument_parser()
        args = parser.parse_args(["--payload-hex", "010203040506", "--timeout", "0.5", "--cr", "3/4"])
        self.assertEqual(args.payload_hex, "010203040506")
        self.assertEqual(args.timeout, 0.5)
        self.assertEqual(args.coding_rate, "3/4")
        self.assertEqual(args.sample_rate, None)
        self.assertIsNone(args.sync)

    def test_transceiver_lifecycle_and_unsynced_send_rejection(self):
        fake_lib = FakeUHDLibrary(rx_samples=b"\x00" * 4096)
        dev = USRPDevice(fake_lib)

        trx = LiveTDDTransceiver(
            sdr_type="usrp",
            sample_rate=2500000.0,
            sync_word="ecee6698",
            usrp_device=dev,
        )

        with trx:
            # 刚启动尚未收到有效包，处于 SEARCHING 态，应拒绝盲发
            res = trx.send_and_wait_reply("01 02 03 04 05 06", sync_timeout=0.05)
            self.assertFalse(res.success)
            self.assertIn("未锁定", res.error)

    def test_transceiver_send_and_wait_reply_when_locked(self):
        fake_lib = FakeUHDLibrary(rx_samples=b"\x00" * 8192)
        dev = USRPDevice(fake_lib)

        trx = LiveTDDTransceiver(
            sdr_type="usrp",
            sample_rate=2500000.0,
            sync_word="ecee6698",
            usrp_device=dev,
        )

        # 模拟同步已锁定
        trx.wait_locked = MagicMock(return_value=True)
        trx.last_snapshot = SyncSnapshot(
            state=SyncState.LOCKED,
            hw_time_s=100.0,
            remote_time_s=10.0,
            timestamp_ticks=1000,
            slot_index=17,
            frequency_hz=2471660000,
            slot_start_hw_time=99.98,
            slot_end_hw_time=100.03,
            slot_remaining_s=0.030,
            next_hop_hw_time=100.03,
            next_slot_index=18,
            next_frequency_hz=2473660000,
            tdd_phase=MagicMock(),
            tdd_window_remaining_s=0.020,
            tdd_mode=MagicMock(),
            turnaround_guard_s=0.0008,
            clock_drift_ppm=1.2,
            residual_s=0.00001,
            uncertainty_s=0.0001,
            confidence=0.98,
            lock_duration_s=5.0,
            anchor_count=10,
            overflow_count=0,
        )

        with trx:
            # 在另一个线程中模拟收到对端回复帧
            def reply_simulator():
                import time
                reply_frame = DecodedFrame(
                    time_s=100.520,
                    frequency_hz=2471660000,
                    cfo_hz=50.0,
                    sample_power_db=25.0,
                    correlation_score=1.0,
                    preamble_errors=0,
                    packet=PhysicalPacket(
                        sync_word=bytes.fromhex("ecee6698"),
                        header=b"\x00\x06",
                        payload=bytes.fromhex("55aa01020304"),
                        crc=b"\x00\x00\x00",
                        fec_path_metric=0.0,
                    ),
                )
                for _ in range(50):
                    time.sleep(0.01)
                    with trx._waiter_lock:
                        if trx._pending_waiters:
                            for min_time, q in list(trx._pending_waiters):
                                q.put_nowait(reply_frame)
                            return

            import threading
            t = threading.Thread(target=reply_simulator)
            t.start()

            res: TDDExchangeResult = trx.send_and_wait_reply(
                "01 02 03 04 05 06",
                wait_reply=True,
                reply_timeout=0.5,
            )
            t.join()

            self.assertTrue(res.success)
            self.assertIsNotNone(res.rx_reply)
            self.assertEqual(res.rx_reply.packet.payload, bytes.fromhex("55aa01020304"))
            self.assertIsNotNone(res.rtt_seconds)

            # 验证硬件层收到了带有 Timed Metadata 的发射调用
            timed_calls = [c for c in fake_lib.dll.calls if c[0] == "tx_metadata_make"]
            self.assertTrue(len(timed_calls) > 0)
            has_time, full, frac = timed_calls[0][1], timed_calls[0][2], timed_calls[0][3]
            self.assertTrue(has_time)  # 精确硬件时间戳发射

    @patch("flrc_modem.duplex_cli.LiveTDDTransceiver")
    def test_duplex_cli_oneshot_mode(self, mock_transceiver_cls):
        instance = mock_transceiver_cls.return_value
        instance.__enter__.return_value = instance
        instance.wait_locked.return_value = True
        instance.send_and_wait_reply.return_value = TDDExchangeResult(
            success=True,
            tx_payload=b"\x01\x02\x03\x04\x05\x06",
            tx_hw_time=100.005,
            tx_slot=17,
            tx_frequency_hz=2471660000,
        )

        code = duplex_cli_main([
            "--args", "addr=192.168.10.2",
            "--payload-hex", "010203040506",
            "--json",
        ])
        self.assertEqual(code, 0)
        instance.send_and_wait_reply.assert_called_once_with(
            "010203040506",
            wait_reply=True,
            reply_timeout=1.0,
        )

    def test_transceiver_send_protocol_dict(self):
        fake_lib = FakeUHDLibrary(rx_samples=b"\x00" * 4096)
        dev = USRPDevice(fake_lib)
        trx = LiveTDDTransceiver(
            sdr_type="usrp",
            sample_rate=2500000.0,
            sync_word="ecee6698",
            protocol="dk32s",
            usrp_device=dev,
        )
        trx.wait_locked = MagicMock(return_value=True)
        trx.last_snapshot = MagicMock(
            state=SyncState.LOCKED,
            frequency_hz=2471660000,
            slot_index=17,
            slot_remaining_s=0.030,
        )
        with trx:
            # 发送结构化字典消息（由 dk32s 插件自动编码）
            res = trx.send_and_wait_reply(
                {"direction": "ground_to_air", "command": "0x11", "fields": {"channels_raw": [1024] * 16}},
                wait_reply=False,
            )
            self.assertTrue(res.success)
            self.assertGreater(len(res.tx_payload), 6)

    def test_transceiver_adaptive_sync_word_learning_and_missing_rejection(self):
        fake_lib = FakeUHDLibrary(rx_samples=b"\x00" * 4096)
        dev = USRPDevice(fake_lib)
        trx = LiveTDDTransceiver(
            sdr_type="usrp",
            sample_rate=2500000.0,
            sync_word=None,  # 不提供默认同步字
            usrp_device=dev,
        )
        trx.wait_locked = MagicMock(return_value=True)
        trx.session.state_machine.predict = MagicMock(return_value=MagicMock(
            slot_remaining_s=0.030,
            frequency_hz=2471660000,
            slot_index=17,
        ))
        with trx:
            # 1. 未提供同步字且状态机尚未捕获到同步字，应拒绝发射
            res = trx.send_and_wait_reply("010203040506", wait_reply=False)
            self.assertFalse(res.success)
            self.assertIn("未指定同步字", res.error)

            # 2. 状态机在空中捕获到帧并学习到同步字
            trx.session.state_machine.sync_word = "ecee6698"
            res2 = trx.send_and_wait_reply("010203040506", wait_reply=False)
            self.assertTrue(res2.success)
            self.assertEqual(res2.tx_slot, 17)


if __name__ == "__main__":
    unittest.main()
