"""同步解调过载恢复：槽位所有权、时间索引和发送门控。"""
from dataclasses import replace
import io
import json
import queue
import unittest
from unittest.mock import patch

from flrc_modem.config import DemodConfig
from flrc_modem.realtime.windows import WindowQueue
from flrc_modem.sync import LiveSyncSession, SyncState
from flrc_modem.io.ui_events import UIEventEmitter
from flrc_modem.sync_cli import _report_live_metrics


class SyncBacklogTests(unittest.TestCase):
    def test_no_frames_still_logs_configuration_and_end_with_session_correlation(self):
        session = LiveSyncSession(sample_rate=2500000)
        session._configured.set()
        session.stats.status = "stopped"
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stderr", errors), _report_live_metrics(session, UIEventEmitter(output, True)):
            pass
        records = [json.loads(line) for line in errors.getvalue().splitlines()]
        self.assertEqual([record["event"] for record in records], ["sync.started", "sync.configured", "sync.stopped"])
        self.assertEqual({record["session_id"] for record in records}, {session.session_id})
        final = records[-1]
        self.assertEqual(final["sample_rate"], 2500000)
        self.assertEqual(final["window_queue_depth"], 128)
        self.assertEqual(final["status"], "stopped")
        self.assertEqual(final["decoded_frames"], 0)

    def test_catch_up_reclaims_only_pending_windows_and_preserves_inflight_data(self):
        config = DemodConfig(sample_rate=2500000, chunk_seconds=.01)
        windows = WindowQueue(config, depth=2, overflow_policy="latest")
        windows.write(b"a" * windows.capacity)
        inflight = windows.ready.get_nowait()
        before = bytes(windows.view(inflight))
        windows.write(b"b" * windows.step_bytes * 2)
        windows.write(b"c" * windows.step_bytes)
        self.assertEqual(bytes(windows.view(inflight)), before)
        self.assertEqual(windows.dropped_windows, 2)
        self.assertEqual(windows.dropped_samples, windows.step_bytes)
        self.assertEqual(windows.drop_metrics()["queue_full_drops"], 2)
        newest = windows.ready.get_nowait()
        self.assertEqual(newest.start_sample, 3 * windows.step_bytes // 2)
        windows.release(inflight)
        windows.release(newest)
        self.assertEqual(windows.free.qsize(), 3)  # 另一个槽位仍属于写入端。

    def test_backlog_invalidates_clock_and_preserves_absolute_uhd_timestamps(self):
        session = LiveSyncSession(sample_rate=2500000, window_queue_depth=2)
        sm = session.state_machine
        for index in range(3):
            sm.fhss.observe(100 + index * .25, 100000 + index * 2500)
        sm.state = SyncState.LOCKED
        sm.last_anchor_hw = 100.5
        self.assertTrue(session.clock_ready_for_transmit)
        windows = session.window_queue
        raw = b"\0" * (windows.capacity + 2 * windows.step_bytes)
        session._metadata_callback(True, 100, .6, len(raw) // 2, 0)
        windows.write(raw)
        self.assertFalse(session.clock_ready_for_transmit)
        metrics = session.realtime_metrics()
        self.assertEqual(metrics["state"], "searching")
        self.assertEqual(metrics["windows_skipped"], 2)
        self.assertEqual(metrics["overflow_count"], 0)
        self.assertEqual(len(sm.fhss.anchors), 0)
        self.assertEqual(session.stats.backlog_recoveries, 1)
        session.realtime_metrics()
        self.assertEqual(session.stats.backlog_recoveries, 1)
        window = windows.ready.get_nowait()
        hw_time, _ = session.extractor.window_time(window.start_sample, window.sample_count)
        self.assertAlmostEqual(hw_time, 100.62)
        windows.release(window)

    def test_stale_window_is_skipped_before_dsp_and_reported_without_frames(self):
        session = LiveSyncSession(sample_rate=2500000, window_queue_depth=2)
        session._on_rate_configured(2500000)
        windows = session.window_queue
        windows.write(b"\0" * windows.capacity)
        stale = replace(windows.ready.get_nowait(), queued_at=0)

        def get_window(**kwargs):
            nonlocal stale
            if stale is not None:
                result, stale = stale, None
                return result
            session.done_event.set()
            raise queue.Empty

        with patch.object(windows.ready, "get", side_effect=get_window), \
             patch("flrc_modem.sync.live.time.monotonic", return_value=1), \
             patch("flrc_modem.sync.live.WindowDecoder.decode") as decode:
            session._dsp_loop()
        decode.assert_not_called()
        metrics = session.realtime_metrics()
        self.assertEqual(metrics["windows_skipped"], 1)
        self.assertEqual(metrics["stale_window_drops"], 1)
        self.assertEqual(metrics["queue_full_drops"], 0)
        self.assertIsNone(session.stats.error)
