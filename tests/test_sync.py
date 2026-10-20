"""FHSS + TDD 同步引擎自动化单元测试套件。

覆盖硬件时间戳提取、跳频时钟拟合与漂移、TDD 转换保护与双模型判定、
状态机全生命周期、UHD Timed Commands 硬件调度及时间轴可视化。
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from flrc_modem.models import DecodedFrame, PhysicalPacket
from flrc_modem.sdr.usrp.abi import UHDRxMetadataErrorCode
from flrc_modem.sync import (
    AD9361TuningBudget,
    FHSSSynchronizer,
    HWTimestamp,
    HWTimestampExtractor,
    LiveSyncSession,
    SyncState,
    SyncStateMachine,
    TDDMode,
    TDDPhase,
    TDDSynchronizer,
    TimelineVisualizer,
    USRPTimedScheduler,
    calculate_flrc_packet_duration,
)
from usrp_fake import FakeUHDLibrary
from flrc_modem.sdr.usrp.device import USRPDevice


SAMPLE_HOPPING_TABLE = [
    2488460000, 2448460000, 2418060000, 2474860000, 2411660000,
    2420860000, 2445660000, 2431660000, 2460860000, 2426060000,
    2482860000, 2454060000, 2437260000, 2414860000, 2451260000,
    2485660000, 2464060000, 2471660000, 2477660000, 2440860000,
]


class HWTimestampTests(unittest.TestCase):
    def test_hw_timestamp_precision_and_advance(self):
        ts = HWTimestamp(full_secs=10, frac_secs=0.5, sample_index=0, sample_rate=2000000.0)
        self.assertAlmostEqual(ts.time_s, 10.5, places=9)

        # 步进 1000 个采样 (0.5ms)
        ts2 = ts.advance_samples(1000)
        self.assertAlmostEqual(ts2.time_s, 10.5005, places=9)
        self.assertAlmostEqual(ts2 - ts, 0.0005, places=9)

    def test_extractor_metadata_and_overflow(self):
        extractor = HWTimestampExtractor(sample_rate=2000000.0)

        # 1. 初始带时间戳
        ts = extractor.on_metadata(has_time=True, full_secs=100, frac_secs=0.25, sample_count=4096)
        self.assertIsNotNone(ts)
        self.assertAlmostEqual(ts.time_s, 100.25, places=7)
        self.assertFalse(extractor.discontinuity_flag)

        # 2. 正常连续无时间戳块
        ts_next = extractor.on_metadata(has_time=False, full_secs=0, frac_secs=0.0, sample_count=4096)
        self.assertIsNotNone(ts_next)
        self.assertAlmostEqual(ts_next.time_s, 100.25 + 4096 / 2000000.0, places=7)

        # 3. 溢出中断
        ts_ov = extractor.on_metadata(
            has_time=False, full_secs=0, frac_secs=0.0, sample_count=0,
            error_code=UHDRxMetadataErrorCode.OVERFLOW
        )
        self.assertIsNone(ts_ov)
        self.assertTrue(extractor.discontinuity_flag)
        self.assertEqual(extractor.overflow_count, 1)

        # 4. 重新收到有效时间戳恢复
        ts_recovered = extractor.on_metadata(has_time=True, full_secs=101, frac_secs=0.1, sample_count=4096)
        self.assertIsNotNone(ts_recovered)
        self.assertFalse(extractor.discontinuity_flag)
        self.assertAlmostEqual(ts_recovered.time_s, 101.1, places=7)

    def test_arrival_time_calculation(self):
        extractor = HWTimestampExtractor(sample_rate=2000000.0)
        chunk_ts = HWTimestamp(full_secs=5, frac_secs=0.0, sample_index=0, sample_rate=2000000.0)

        # 假设前导峰位于采样点 5000，滤波器时延为 50 采样
        arrival = extractor.calculate_arrival(chunk_ts, peak_sample_index=5000, filter_delay_samples=50.0)
        expected_s = 5.0 + (5000 - 50.0) / 2000000.0
        self.assertAlmostEqual(arrival.time_s, expected_s, places=9)


class FHSSSynchronizerTests(unittest.TestCase):
    def test_perfect_clock_observation_and_slot_prediction(self):
        sync = FHSSSynchronizer(SAMPLE_HOPPING_TABLE, dwell_seconds=0.050, min_fit_span=0.2)

        # 模拟无频偏的连续观测：基准点 t_hw=1.0s, ticks=10000 (1.0s)
        # 每个时隙 50ms (500 ticks)，每 12ms (120 ticks) 一个包
        t_hw = 1.0
        ticks = 10000
        for _ in range(25):  # 25 * 0.012 = 0.3s >= min_fit_span(0.2s)
            sync.observe(t_hw, ticks)
            t_hw += 0.012
            ticks += 120

        self.assertTrue(sync.locked)
        self.assertAlmostEqual(sync.rate, 1.0, places=6)

        # 预测 t_hw = 1.0 时
        pred = sync.predict(1.0)
        # ticks=10000, (10000 % 10000) // 500 = 0
        self.assertEqual(pred.slot_index, 0)
        self.assertEqual(pred.frequency_hz, SAMPLE_HOPPING_TABLE[0])
        self.assertAlmostEqual(pred.slot_start_hw_time, 1.0, places=4)
        self.assertAlmostEqual(pred.slot_end_hw_time, 1.050, places=4)
        self.assertAlmostEqual(pred.slot_remaining_s, 0.050, places=4)
        self.assertEqual(pred.next_slot_index, 1)
        self.assertEqual(pred.next_frequency_hz, SAMPLE_HOPPING_TABLE[1])

        # 预测第 17 个时隙 (t = 1.0 + 17 * 0.050 = 1.850s)
        pred_slot17 = sync.predict(1.855)
        self.assertEqual(pred_slot17.slot_index, 17)
        self.assertEqual(pred_slot17.frequency_hz, SAMPLE_HOPPING_TABLE[17])

    def test_clock_drift_tracking_ppm(self):
        sync = FHSSSynchronizer(SAMPLE_HOPPING_TABLE, dwell_seconds=0.050, min_fit_span=0.5, window=128)

        # 模拟远端时钟偏快 +50 ppm (rate = 1.000050)
        # 1 tick = 100µs，测量 50ppm 需观察足够时间以克服整刻度量化
        rate = 1.000050
        t_hw_start = 2.0
        remote_start = 5.0

        for i in range(120):  # 120 * 0.025s = 3.0s
            t_hw = t_hw_start + i * 0.025
            remote = remote_start + rate * (t_hw - t_hw_start)
            ticks = round(remote * 10000) % (1 << 32)
            sync.observe(t_hw, ticks)

        self.assertTrue(sync.locked)
        pred = sync.predict(t_hw_start + 3.0)
        self.assertAlmostEqual(pred.clock_drift_ppm, 50.0, delta=6.0)

    def test_32bit_tick_wraparound_handling(self):
        sync = FHSSSynchronizer(SAMPLE_HOPPING_TABLE, dwell_seconds=0.050, min_fit_span=0.02)

        # 模拟在 2^32 - 100 附近的卷绕
        max_tick = (1 << 32)
        ticks_near_wrap = max_tick - 200
        sync.observe(10.0, ticks_near_wrap)
        sync.observe(10.012, ticks_near_wrap + 120)
        # 发生卷绕到 40
        sync.observe(10.024, (ticks_near_wrap + 240) % max_tick)
        sync.observe(10.036, (ticks_near_wrap + 360) % max_tick)

        pred = sync.predict(10.040)
        self.assertGreater(pred.remote_time_s, (max_tick / 10000.0) - 1.0)

    def test_outlier_gating_and_invalidation(self):
        sync = FHSSSynchronizer(SAMPLE_HOPPING_TABLE, min_fit_span=0.08)
        sync.observe(1.0, 10000)
        sync.observe(1.05, 10500)
        sync.observe(1.10, 11000)
        self.assertTrue(sync.locked)

        # 注入一个偏差 1 秒的异常时间戳
        rejected = sync.observe(1.12, 21200)
        self.assertFalse(rejected)
        self.assertEqual(sync.rejected_count, 1)

        # 显式失效
        sync.invalidate("模拟硬件丢样")
        self.assertFalse(sync.locked)
        self.assertEqual(len(sync.anchors), 0)


class TDDSynchronizerTests(unittest.TestCase):
    def test_packet_duration_calculation(self):
        # 33 字节，325kbps，CR=3/4
        # (53+32 + 16*4/3 + (33+3)*8*4/3 + 6) / 325000 = (85 + 21.33 + 384 + 6) / 325000 ≈ 496.33 / 325000 ≈ 1.527 ms
        dur_33 = calculate_flrc_packet_duration(33, 325000, "3/4")
        self.assertAlmostEqual(dur_33 * 1000, 1.527, delta=0.01)

        # 7 字节
        # (85 + 21.33 + (7+3)*8*4/3 + 6) / 325000 = (85 + 21.33 + 106.67 + 6) / 325000 ≈ 219 / 325000 ≈ 0.673 ms
        dur_7 = calculate_flrc_packet_duration(7, 325000, "3/4")
        self.assertAlmostEqual(dur_7 * 1000, 0.673, delta=0.01)

    def test_turnaround_observation_and_smoothing(self):
        tdd = TDDSynchronizer(mode=TDDMode.RECEPTION_TRIGGERED)

        # 地面开始于 1.0s (33B 发送 ~1.53ms, 结束于 1.001527s)
        # 天空到达于 1.002337s (转换时间 = 1.002337 - 1.001527 = 0.000810s = 810µs)
        tx_dur = calculate_flrc_packet_duration(33, 325000, "3/4")
        tdd.observe_turnaround(1.0, 1.0 + tx_dur + 0.000810, ground_len=33)
        self.assertAlmostEqual(tdd.estimated_turnaround_s * 1e6, 810.0, places=1)

    def test_phase_classification_models(self):
        tdd = TDDSynchronizer(mode=TDDMode.RECEPTION_TRIGGERED, slot_dwell_s=0.050, slot_guard_s=0.011)
        slot_start = 10.0
        ground_tx = 10.005

        # 1. 正在地面发送
        win_tx = tdd.classify_phase(10.006, slot_start, last_ground_tx_hw_time=ground_tx)
        self.assertEqual(win_tx.phase, TDDPhase.GROUND_TX)

        # 2. 转换保护间隔
        tx_end = ground_tx + tdd.ground_duration_s
        win_guard = tdd.classify_phase(tx_end + 0.0004, slot_start, last_ground_tx_hw_time=ground_tx)
        self.assertEqual(win_guard.phase, TDDPhase.TURNAROUND_GUARD)

        # 3. 槽尾守候期 (10.0 + 50ms - 11ms = 10.039s 之后)
        win_slot_guard = tdd.classify_phase(10.042, slot_start, last_ground_tx_hw_time=ground_tx)
        self.assertEqual(win_slot_guard.phase, TDDPhase.SLOT_GUARD)


class SyncStateMachineTests(unittest.TestCase):
    def _create_fake_frame(self, time_s, direction, ticks=None, sync_hex="ecee6698", payload=b"\x00"*33):
        packet = PhysicalPacket(
            sync_word=bytes.fromhex(sync_hex),
            header=b"\x00\x21",
            payload=payload,
            crc=b"\x11\x22\x33",
            fec_path_metric=0.0,
        )
        frame = DecodedFrame(
            time_s=time_s,
            frequency_hz=2480000000,
            cfo_hz=100.0,
            sample_power_db=-20.0,
            correlation_score=1.2,
            preamble_errors=0,
            packet=packet,
        )
        object.__setattr__(frame, "application", {
            "direction": direction,
            "timestamp_ticks": ticks,
            "sum8_ok": True,
        })
        return frame

    def test_state_machine_full_lifecycle(self):
        sm = SyncStateMachine(SAMPLE_HOPPING_TABLE, holdover_timeout_s=0.4)
        self.assertEqual(sm.state, SyncState.SEARCHING)

        # 1. 收到第一个有效包 -> ACQUIRING
        f1 = self._create_fake_frame(1.000, "ground_to_air", ticks=10000)
        sm.on_frame(1.000, f1)
        self.assertEqual(sm.state, SyncState.ACQUIRING)

        # 2. 连续收到有效包，跨度超 0.4s -> LOCKED
        for i in range(1, 35):
            t = 1.000 + i * 0.012
            ticks = 10000 + i * 120
            f = self._create_fake_frame(t, "ground_to_air", ticks=ticks)
            sm.on_frame(t, f)

        self.assertEqual(sm.state, SyncState.LOCKED)
        snap = sm.predict(1.500)
        self.assertEqual(snap.state, SyncState.LOCKED)
        self.assertGreater(snap.confidence, 0.7)

        # 3. 超过 0.15s 未收到新包 -> HOLDOVER
        snap_hold = sm.predict(1.700)
        self.assertEqual(snap_hold.state, SyncState.HOLDOVER)

        # 4. 超过 0.4s 未收到新包 -> LOST
        snap_lost = sm.predict(2.000)
        self.assertEqual(snap_lost.state, SyncState.LOST)

        # 5. 再次调用 step 恢复为 SEARCHING
        sm.step(2.001)
        self.assertEqual(sm.state, SyncState.SEARCHING)

    def test_discontinuity_overflow_handling(self):
        sm = SyncStateMachine(SAMPLE_HOPPING_TABLE)
        for i in range(35):
            t = 1.000 + i * 0.012
            sm.on_frame(t, self._create_fake_frame(t, "ground_to_air", ticks=10000 + i * 120))
        self.assertEqual(sm.state, SyncState.LOCKED)

        # 模拟发生 OVERFLOW
        sm.on_discontinuity("硬件溢出")
        self.assertEqual(sm.state, SyncState.SEARCHING)
        self.assertEqual(sm.overflow_count, 1)


class USRPTimedSchedulerTests(unittest.TestCase):
    def test_ad9361_budget_evaluation(self):
        fake_lib = FakeUHDLibrary()
        dev = USRPDevice(fake_lib)
        with dev:
            scheduler = USRPTimedScheduler(
                dev,
                command_lead_time_s=0.005,
                ad9361_settling_s=0.0005,
                slot_guard_s=0.011,
            )
            budget: AD9361TuningBudget = scheduler.evaluate_tuning_budget()
            self.assertTrue(budget.is_safe)
            self.assertAlmostEqual(budget.safe_margin_s * 1000, 5.5, places=2)

    def test_schedule_rx_and_tx_timed_commands(self):
        fake_lib = FakeUHDLibrary()
        dev = USRPDevice(fake_lib)
        with dev:
            scheduler = USRPTimedScheduler(dev)
            rx_cmd = scheduler.schedule_rx_hop(2480000000, hop_hw_time=100.5, slot_index=3)
            self.assertEqual(rx_cmd.frequency_hz, 2480000000)
            self.assertEqual(rx_cmd.slot_index, 3)

            # 验证底层 Fake UHD 记录到了 set_command_time 与 clear_command_time 调用
            calls = fake_lib.dll.calls
            self.assertIn(("set_command_time", 100, 0.5, 0), calls)
            self.assertIn(("clear_command_time", 0), calls)


class TimelineVisualizerTests(unittest.TestCase):
    def test_render_ascii_svg_and_html(self):
        viz = TimelineVisualizer(start_time_s=1.0, end_time_s=1.050)
        viz.add_event("Slot 17", "fhss_slot", 1.0, 1.050, details="2471.66MHz")
        viz.add_event("TX1", "ground_tx", 1.0017, 1.0032)
        viz.add_event("Guard", "turnaround", 1.0032, 1.0040)
        viz.add_event("RX1", "air_rx", 1.0040, 1.0046)
        viz.add_event("Guard", "slot_guard", 1.039, 1.050)
        viz.add_event("Actual #1", "actual_arrival", 1.0017, 1.0017)
        viz.add_event("Pred #1", "predicted_arrival", 1.001705, 1.001705, error_us=+5.0)

        # 1. ASCII 渲染
        ascii_txt = viz.render_ascii(width=60)
        self.assertIn("FHSS:", ascii_txt)
        self.assertIn("TDD :", ascii_txt)
        self.assertIn("+5.00 us", ascii_txt)

        # 2. SVG 渲染
        svg_txt = viz.render_svg()
        self.assertTrue(svg_txt.startswith("<svg"))
        self.assertTrue(svg_txt.endswith("</svg>"))
        self.assertIn("Slot 17", svg_txt)

        # 3. HTML 渲染
        html_txt = viz.render_html()
        self.assertIn("<!DOCTYPE html>", html_txt)
        self.assertIn("<svg", html_txt)


class RealCaptureReplayTests(unittest.TestCase):
    """回放真实的 outputs/passive-current-20261020.jsonl 录制数据核验引擎。"""

    def test_replay_passive_72_frames(self):
        passive_path = Path("outputs/passive-current-20261020.jsonl")
        if not passive_path.is_file():
            self.skipTest("真实捕获文件 outputs/passive-current-20261020.jsonl 不存在")

        records = [json.loads(line) for line in passive_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(records), 72)

        sm = SyncStateMachine(SAMPLE_HOPPING_TABLE, sync_word="ecee6698")

        residuals = []
        for r in records:
            app = r.get("application", {})
            f = DecodedFrame(
                time_s=r["time_s"],
                frequency_hz=r["frequency_hz"],
                cfo_hz=r.get("cfo_hz", 0.0),
                sample_power_db=r.get("sample_power_db", 0.0),
                correlation_score=r.get("correlation_score", 1.0),
                preamble_errors=0,
                packet=PhysicalPacket(
                    sync_word=bytes.fromhex(r["sync_word_hex"]),
                    header=bytes.fromhex(r.get("hardware_header_hex", "0021")),
                    payload=bytes.fromhex(r["payload_hex"]),
                    crc=bytes.fromhex(r["crc_hex"]),
                    fec_path_metric=0.0,
                ),
            )
            object.__setattr__(f, "application", app)
            accepted = sm.on_frame(r["time_s"], f)
            if accepted and sm.state == SyncState.LOCKED:
                pred = sm.predict(r["time_s"])
                # 校验预测的载波频率与真实帧载频完全一致
                self.assertEqual(pred.frequency_hz, round(r["frequency_hz"]))
                residuals.append(pred.residual_s)

        self.assertEqual(sm.state, SyncState.LOCKED)
        self.assertGreater(len(residuals), 20)
        # 拟合残差在微秒级
        self.assertLess(max(residuals), 0.001)  # < 1ms


class LiveSyncSessionTests(unittest.TestCase):
    def test_live_sync_session_lifecycle_with_fake_usrp(self):
        fake_lib = FakeUHDLibrary(rx_samples=b"\x00" * 8192)
        dev = USRPDevice(fake_lib)
        session = LiveSyncSession(
            sdr_type="usrp",
            duration=0.002,
            usrp_device=dev,
        )
        with session:
            events = list(session.events())
        self.assertEqual(session.stats.status, "stopped")
        self.assertGreaterEqual(session.stats.total_bytes, 0)

    def test_live_sync_session_default_sample_rate_for_n310(self):
        s1 = LiveSyncSession(sdr_type="usrp")
        self.assertEqual(s1.sample_rate, 2500000.0)

        s2 = LiveSyncSession(sdr_type="usrp", master_clock_rate=122.88e6)
        self.assertEqual(s2.sample_rate, 1920000.0)

        s3 = LiveSyncSession(sdr_type="hackrf")
        self.assertEqual(s3.sample_rate, 2000000.0)

    def test_sync_cli_help(self):
        from flrc_modem.sync_cli import make_argument_parser
        parser = make_argument_parser()
        self.assertEqual(parser.prog, "flrc sync")


if __name__ == "__main__":
    unittest.main()
