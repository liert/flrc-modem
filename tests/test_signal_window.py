"""按帧定位 IQ：时间换算、限量读取、文件边界及关联校验回归。"""
import json

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.io.iq import encode_samples
from webui.backend.app.artifacts.manager import ArtifactManager
from webui.backend.app.artifacts.lifecycle import file_version
from webui.backend.app.main import app
from webui.backend.app.storage.db import Database


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    from webui.backend.app.storage import db as db_module
    from webui.backend.app.artifacts import manager as manager_module
    db = Database(tmp_path / "window.db")
    monkeypatch.setattr(db_module, "_DB", db)
    monkeypatch.setattr(manager_module, "ALLOWED_DIRS", [tmp_path])
    manager = ArtifactManager()
    monkeypatch.setattr(manager_module, "_ARTIFACT_MGR", manager)
    return db, manager


def add_run(db, path, frames, *, run_id="run", command="demod", rate=64000, iq_format="cf32"):
    db.insert_run({"id": run_id, "command_id": command, "status": "SUCCEEDED", "created_at": "now",
                   "command_args": {"iq": str(path), "output": str(frames)}, "cli_argv": []})
    db.insert_artifact(run_id, None, str(path), "input_iq", {
        "sample_rate": rate, "center_frequency": 2403000000, "iq_format": iq_format, "file_version": file_version(path),
    }, "now")
    frames.write_text(json.dumps({"time_s": .5, "frequency_hz": 2403004000, "payload_hex": "123456", "crc_ok": True}) + '\n', encoding="utf-8")
    db.insert_artifact(run_id, None, str(frames), "frames_jsonl", {"file_version": file_version(frames)}, "now")


def pulse(path):
    samples = np.zeros(64000, dtype=np.complex64)
    samples[32000:32512] = .75 * np.exp(2j * np.pi * np.arange(512) / 16)
    path.write_bytes(encode_samples(samples, "cf32"))


@pytest.mark.anyio
async def test_frame_selection_reads_signal_instead_of_quiet_file_start_once(tmp_path, fixture, monkeypatch):
    db, manager = fixture
    path, frames = tmp_path / "pulse.cf32", tmp_path / "frames.jsonl"
    pulse(path)
    add_run(db, path, frames)
    reads = []
    read = manager.read_iq_samples
    def track(*args, **kwargs):
        reads.append(kwargs)
        return read(*args, **kwargs)
    monkeypatch.setattr(manager, "read_iq_samples", track)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        initial = (await client.get("/api/v1/analysis/window", params={"run_id": "run", "n_fft": 256})).json()
        assert initial["spectrum"]["peak_power_db"] == -120
        reads.clear()
        response = await client.get("/api/v1/analysis/window", params={"run_id": "run", "frame_time_s": .5, "n_fft": 256})
        assert response.status_code == 200
        window = response.json()
        assert len(reads) == 1 and reads[0]["offset_samples"] == 31968
        assert reads[0]["max_samples"] == 320
        assert window["frame_sample"] == window["fft_offset_samples"] == 32000
        assert window["start_time_s"] == 31968 / 64000
        assert window["end_time_s"] == 32288 / 64000
        assert window["spectrum"]["peak_frequency_hz"] == 2403004000
        assert window["spectrum"]["peak_power_db"] > -60
        assert all(abs(abs(complex(i, q)) - .75) < .0001 for i, q in window["constellation"]["points"])
        assert window["waveform"]["points"][0][0] == window["start_time_s"] * 1000


@pytest.mark.parametrize("iq_format", ["cs8", "cu8", "cf32"])
def test_all_iq_formats_use_correct_byte_offset(tmp_path, fixture, iq_format):
    _, manager = fixture
    samples = np.zeros(6400, np.complex64)
    samples[3200:3456] = .5 + .25j
    path = tmp_path / f"signal.{iq_format}"
    path.write_bytes(encode_samples(samples, iq_format))
    window = manager.compute_window(path, iq_format=iq_format, sample_rate=64000, center_freq=0, frame_time_s=.05, n_fft=256)
    assert window["frame_sample"] == 3200
    assert window["constellation"]["points"][0] == [.5, .25]


def test_file_edges_fft_shrinking_and_out_of_range(tmp_path, fixture):
    _, manager = fixture
    path = tmp_path / "signal.cf32"
    path.write_bytes(encode_samples(np.ones(6400, np.complex64), "cf32"))
    args = dict(iq_format="cf32", sample_rate=64000, center_freq=0, n_fft=256)
    start = manager.compute_window(path, frame_time_s=1 / 64000, **args)
    assert start["offset_samples"] == 0 and start["frame_sample"] == 1
    near_end = manager.compute_window(path, frame_time_s=6300 / 64000, **args)
    assert near_end["truncated"] and near_end["spectrum"]["n_fft"] == 64
    assert near_end["fft_sample_count"] == 64
    assert near_end["end_time_s"] == .1
    last = manager.compute_window(path, frame_time_s=6399 / 64000, **args)
    assert last["spectrum"]["points"] == [] and last["constellation"]["points"] == [[1, 0]]
    for frame_time in (.1, 1e308):
        with pytest.raises(ValueError, match="超出"):
            manager.compute_window(path, frame_time_s=frame_time, **args)


def test_display_reduction_preserves_short_pulse_and_limits_points(tmp_path, fixture):
    _, manager = fixture
    path = tmp_path / "pulse.cf32"
    samples = np.zeros(16000, np.complex64)
    samples[1537] = 1
    path.write_bytes(encode_samples(samples, "cf32"))
    window = manager.compute_window(path, iq_format="cf32", sample_rate=800000, center_freq=0, window_ms=10)
    points = window["waveform"]["points"]
    assert len(points) <= 2048
    assert max(point[3] for point in points) == 1
    assert any(point[0] == 1537 / 800000 * 1000 for point in points)
    assert points[0][0] == 0 and points[-1][0] == 7999 / 800000 * 1000
    with pytest.raises(ValueError, match="1048576"):
        manager.compute_window(path, iq_format="cf32", sample_rate=1e9, center_freq=0)


@pytest.mark.anyio
async def test_window_rejects_hardware_time_missing_files_and_invalid_queries(tmp_path, fixture):
    db, _ = fixture
    path, frames = tmp_path / "pulse.cf32", tmp_path / "frames.jsonl"
    pulse(path)
    add_run(db, path, frames, command="sync")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/analysis/window", params={"run_id": "run", "frame_time_s": .5})).status_code == 409
        assert (await client.get("/api/v1/analysis/window", params={"run_id": "missing"})).status_code == 404
        for params in ({"frame_time_s": -1}, {"window_ms": 21}, {"frame_time_s": "nan"}):
            assert (await client.get("/api/v1/analysis/window", params={"run_id": "run", **params})).status_code == 422
        assert (await client.get("/api/v1/analysis/window", params={"run_id": "run", "n_fft": 96})).status_code == 400
        path.unlink()
        assert (await client.get("/api/v1/analysis/window", params={"run_id": "run"})).status_code == 404


@pytest.mark.anyio
async def test_window_checks_iq_version_after_read_and_rejects_overwritten_frames(tmp_path, fixture, monkeypatch):
    db, manager = fixture
    path, frames = tmp_path / "pulse.cf32", tmp_path / "frames.jsonl"
    pulse(path)
    add_run(db, path, frames)
    compute = manager.compute_window
    def replace(*args, **kwargs):
        window = compute(*args, **kwargs)
        path.write_bytes(b'\0' * 16)
        return window
    monkeypatch.setattr(manager, "compute_window", replace)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/analysis/window", params={"run_id": "run", "frame_time_s": .5})).status_code == 409
        pulse(path)
        add_run(db, path, frames, run_id="new")
        assert (await client.get("/api/v1/analysis/window", params={"run_id": "run", "frame_time_s": .5})).status_code == 409
