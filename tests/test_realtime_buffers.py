"""CLI、同步监听、频谱共用的缓冲契约；不依赖线程调度或真实硬件。"""
from dataclasses import replace
import queue

import numpy as np
import pytest

from flrc_modem.config import DemodConfig
from flrc_modem.io.iq import SAMPLE_BYTES, encode_samples, iter_iq_chunks
from flrc_modem.realtime.windows import IQBlockQueue, RealtimeBacklogError, WindowQueue


@pytest.fixture(params=["cs8", "cf32"])
def config(request):
    return DemodConfig(sample_rate=2_000_000, iq_format=request.param, chunk_seconds=.005)


def drain(windows):
    result = []
    while not windows.ready.empty():
        window = windows.ready.get_nowait()
        result.append((window.start_sample / windows.sample_rate, bytes(windows.view(window))))
        windows.release(window)
    return result


def test_fragmented_windows_match_offline_with_short_and_long_overlap(config, tmp_path):
    raw = encode_samples(np.linspace(-.5, .5, 30_001).astype(np.complex64), config.iq_format)
    path = tmp_path / "windows.iq"
    path.write_bytes(raw)
    for seconds in (.001, .010):
        cfg = replace(config, chunk_seconds=seconds)
        windows = WindowQueue(cfg, depth=64)
        actual = []
        # Drivers may split a sample across writes; only EOF must be aligned.
        for offset in range(0, len(raw), 997):
            part = raw[offset:offset + 997]
            assert windows.write(part) == len(part)
            actual.extend(drain(windows))
        windows.finish()
        actual.extend(drain(windows))
        expected = [(start, encode_samples(iq, cfg.iq_format)) for start, iq in iter_iq_chunks(path, cfg)]
        assert actual == expected
        assert windows.input_bytes == len(raw)


@pytest.mark.parametrize("policy", ["error", "latest"])
def test_overload_preserves_inflight_slot_and_sample_indices(config, policy):
    windows = WindowQueue(config, depth=2, overflow_policy=policy)
    windows.write(b"a" * windows.capacity)
    inflight = windows.ready.get_nowait()
    before = bytes(windows.view(inflight))
    windows.write(b"b" * windows.step_bytes * 2)
    if policy == "error":
        with pytest.raises(RealtimeBacklogError):
            windows.write(b"c" * windows.step_bytes)
    else:
        windows.write(b"c" * windows.step_bytes)
        assert windows.dropped_windows == 2
        assert windows.dropped_samples == 2 * windows.step_bytes // windows.sample_bytes
        assert windows.drop_metrics()["queue_full_drops"] == 2
        newest = windows.ready.get_nowait()
        assert newest.start_sample == 3 * windows.step_bytes // windows.sample_bytes
        windows.release(newest)
    assert bytes(windows.view(inflight)) == before
    windows.release(inflight)
    drain(windows)
    assert windows.free.qsize() == 3


def test_incomplete_sample_and_abort(config):
    windows = WindowQueue(config)
    windows.write(b"x")
    with pytest.raises(ValueError, match="不完整"):
        windows.finish()
    aborted = WindowQueue(config)
    aborted.abort.set()
    assert aborted.write(b"abc") == 3
    assert aborted.discarded_bytes == 3
    assert aborted.input_bytes == 0


def test_gap_resets_partial_overlap_and_reanchors_time(config):
    windows = WindowQueue(config)
    windows.write_block(b"a" * windows.capacity, 100)
    old = windows.ready.get_nowait()
    windows.write(b"b" * windows.sample_bytes * 3)
    windows.write_block(b"c" * windows.capacity, 200, discontinuity=True)
    new = windows.ready.get_nowait()
    assert bytes(windows.view(old)) == b"a" * windows.capacity
    assert bytes(windows.view(new)) == b"c" * windows.capacity
    assert old.start_time_s == 100
    assert new.start_time_s == pytest.approx(200)
    assert new.epoch == old.epoch + 1
    assert new.start_sample == (windows.capacity + 3 * windows.sample_bytes) // windows.sample_bytes
    assert windows.drop_metrics()["partial_samples_discarded"] == 3
    windows.release(old)
    windows.release(new)


def test_synchronous_large_block_has_no_artificial_backlog(config):
    windows = WindowQueue(config, depth=2)
    count = 0
    raw = b"a" * (windows.capacity + windows.step_bytes * 20)
    for window in windows.iter_block(raw, 123):
        assert window.start_time_s == pytest.approx(123 + count * windows.step_bytes / windows.sample_bytes / config.sample_rate)
        assert bytes(windows.view(window)) == b"a" * windows.capacity
        windows.release(window)
        count += 1
    assert count == 21
    assert windows.dropped_windows == 0


@pytest.mark.parametrize("depth", [1, 3])
def test_raw_overload_marks_first_survivor_and_preserves_gap_through_repeated_drops(config, depth):
    blocks = IQBlockQueue(depth, SAMPLE_BYTES[config.iq_format])
    for index in range(depth + 2):
        blocks.put_latest(bytes([index]) * blocks.sample_bytes * (index + 1), index, gap=index == 0)
    assert blocks.dropped_blocks == 2
    assert blocks.dropped_samples == 3
    for index in range(2, depth + 2):
        raw, timestamp, gap = blocks.get_nowait()
        assert raw == bytes([index]) * blocks.sample_bytes * (index + 1)
        assert timestamp == index
        assert gap == (index == 2)
    with pytest.raises(queue.Empty):
        blocks.get_nowait()
