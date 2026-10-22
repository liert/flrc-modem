"""WebUI 生命周期、边界、硬件枚举与持久化回归；不启动射频收发。"""
import asyncio
import json
import sqlite3
import sys
from unittest.mock import Mock

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.io.iq import encode_samples
from tests.hackrf_fake import FakeAPI
from webui.backend.app.artifacts.manager import ArtifactManager
from webui.backend.app.commands.registry import CommandRegistry
from webui.backend.app.devices.manager import DeviceManager
from webui.backend.app.events.hub import EventHub
from webui.backend.app.jobs.manager import JobManager
from webui.backend.app.main import app
from webui.backend.app.storage.db import Database
from webui.backend.app.templates.manager import TemplateManager


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from webui.backend.app.storage import db as db_module
    from webui.backend.app.devices import manager as devices_module
    from webui.backend.app.events import hub as hub_module
    db = Database(tmp_path / "webui.db")
    devices = DeviceManager()
    monkeypatch.setattr(db_module, "_DB", db)
    monkeypatch.setattr(devices_module, "_DEVICE_MGR", devices)
    monkeypatch.setattr(hub_module, "_EVENT_HUB", EventHub())
    return db, devices


def test_templates_validate_before_replacing_existing_content(tmp_path):
    manager = TemplateManager(tmp_path)
    manager.save_template("good.json", '{"command": "0x11"}')
    for name, content in (("good.json", "{"), ("good.json", "[]"), ("good.json", '{"x": NaN}'),
                          ("script.py", "print(1)"), ("../outside.json", "{}")):
        with pytest.raises((ValueError, PermissionError)):
            manager.save_template(name, content)
    assert (tmp_path / "good.json").read_text() == '{"command": "0x11"}'
    (tmp_path / "bad.json").write_text("[]")
    (tmp_path / "bad.toml").write_text("[bad")
    assert [item.name for item in manager.list_templates()] == ["good.json"]
    manager.save_template("good.toml", "[demod]\nbit_rate=1300000\n")
    with pytest.raises(ValueError):
        manager.save_template("good.toml", "[demod")
    assert manager.get_template("good.toml").parsed["demod"]["bit_rate"] == 1300000


def test_cli_rejects_invalid_values_and_preserves_numeric_zero():
    registry = CommandRegistry()
    valid = {"iq": "capture.iq", "bt": "0"}
    argv = registry.build_cli_argv("demod", valid)
    assert argv[argv.index("--bt") + 1] == "0.0"
    for values in ({"wrong": 1}, {"bt": "0.7"}, {"max_seconds": "NaN"}, {"iq": ""}):
        with pytest.raises(ValueError):
            registry.build_cli_argv("demod", {"iq": "capture.iq", **values})
    with pytest.raises(ValueError):
        registry.build_cli_argv("modulate", {"payload_hex": "001122334455", "sync": "54455354", "output": "x.iq", "verify": "false"})
    assert registry.build_cli_argv("hackrf", {}, True)[-1] == "info"
    assert "--ui-events" not in registry.build_cli_argv("usrp", {"args": "a", "action": "probe"})


def test_hackrf_probe_enumerates_without_opening_device(monkeypatch):
    from webui.backend.app.devices.probe import probe_hackrf
    fake = FakeAPI()
    monkeypatch.setattr("flrc_modem.sdr.hackrf.library.HackRFLibrary", lambda: fake)
    device = probe_hackrf()
    assert device.status == "online"
    assert device.details["count"] == 1
    assert fake.calls == ["acquire", "device_list", "free_list", "release"]


def test_busy_hardware_is_not_probed():
    manager = DeviceManager()
    manager.acquire_device("hackrf", "run_1")
    manager.acquire_device("usrp", "run_1")
    manager._probe_hackrf = Mock(side_effect=AssertionError("must not probe"))
    manager._probe_usrp = Mock(side_effect=AssertionError("must not probe"))
    assert all(device.status == "busy" for device in manager.list_devices())


def test_database_returns_log_tail_and_event_arrival_order_and_closes_connections(tmp_path):
    database = Database(tmp_path / "test.db")
    for i in range(5):
        database.insert_log("run", "stdout", str(i), str(i))
    assert [row["line"] for row in database.get_logs("run", 2)] == ["3", "4"]
    for seq in (1, 999998, 2):
        database.insert_event("run", seq, "test", None, "now", {})
    assert [row["seq"] for row in database.get_events("run")] == [1, 999998, 2]
    with database._get_connection() as connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    for i in range(201):
        database.insert_artifact("run", None, f"{i}.json", "file", {}, "now")
    assert database.get_artifact(1)["path"] == "0.json"


def test_iq_normalization_and_psd_integral(tmp_path, monkeypatch):
    from webui.backend.app.artifacts import manager as artifact_module
    monkeypatch.setattr(artifact_module, "ALLOWED_DIRS", [tmp_path])
    manager = ArtifactManager()
    iq = np.exp(2j * np.pi * np.arange(4096) / 16).astype(np.complex64)
    path = tmp_path / "signal.cf32"
    path.write_bytes(encode_samples(iq, "cf32"))
    result = manager.compute_spectrum(path, "cf32", n_fft=1024, sample_rate=8192, center_freq=1000000)
    assert result["peak_frequency_hz"] == 1000512
    integral = sum(10 ** (db / 10) for _, db in result["points"]) * 8
    assert integral == pytest.approx(1, rel=.01)
    path = tmp_path / "zero.cu8"
    path.write_bytes(bytes([128, 128]) * 16)
    assert not np.any(manager.read_iq_samples(path, "cu8"))
    path = tmp_path / "nan.cf32"
    path.write_bytes(np.array([np.nan, 0], dtype="<f4").tobytes())
    with pytest.raises(ValueError):
        manager.read_iq_samples(path, "cf32")


@pytest.mark.anyio
async def test_analysis_query_boundaries_and_cross_origin_preflight():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for query in ("n_fft=1000000000", "sample_rate=0", "sample_rate=nan", "offset_samples=-1"):
            response = await client.get(f"/api/v1/analysis/spectrum?path=outputs/a.iq&{query}")
            assert response.status_code == 422
        assert (await client.get("/api/v1/analysis/constellation?path=a&max_points=0")).status_code == 422
        assert (await client.get("/api/v1/runs?limit=-1")).status_code == 422
        response = await client.options("/api/v1/runs", headers={"origin": "https://untrusted.example",
                                        "access-control-request-method": "POST"})
        assert response.status_code == 400


@pytest.mark.anyio
async def test_job_cancel_before_spawn(isolated, monkeypatch):
    db, devices = isolated
    from webui.backend.app.jobs import manager as job_module
    spawn = Mock(side_effect=AssertionError("queued cancellation must not spawn"))
    monkeypatch.setattr(job_module.asyncio, "create_subprocess_exec", spawn)
    manager = JobManager()
    record = await manager.create_and_start_run("live", {})
    assert record.sdr_type == "hackrf"
    assert await manager.stop_run(record.id)
    assert db.get_run(record.id)["status"] == "CANCELLED"
    assert devices.get_lock_info("hackrf") is None
    assert not manager._tasks
    assert not manager._stop_requests


@pytest.mark.anyio
async def test_job_failure_to_persist_releases_lock(isolated, monkeypatch):
    db, devices = isolated
    monkeypatch.setattr(db, "insert_run", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        await JobManager().create_and_start_run("live", {})
    assert devices.get_lock_info("hackrf") is None


@pytest.mark.anyio
async def test_cancel_during_spawn_waits_for_exit_before_unlock(isolated, monkeypatch):
    db, devices = isolated
    from webui.backend.app.jobs import manager as job_module
    original_spawn = asyncio.create_subprocess_exec
    started, proceed = asyncio.Event(), asyncio.Event()
    child = []
    async def delayed_spawn(*args, **kwargs):
        if args[0] == "taskkill":
            return await original_spawn(*args, **kwargs)
        started.set()
        await proceed.wait()
        proc = await original_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        child.append(proc)
        return proc
    monkeypatch.setattr(job_module.asyncio, "create_subprocess_exec", delayed_spawn)
    manager = JobManager()
    record = await manager.create_and_start_run("live", {})
    await asyncio.wait_for(started.wait(), 2)
    stopping = asyncio.create_task(manager.stop_run(record.id))
    await asyncio.sleep(0)
    assert devices.get_lock_info("hackrf") is not None
    proceed.set()
    assert await asyncio.wait_for(stopping, 12)
    assert child[0].returncode is not None
    assert devices.get_lock_info("hackrf") is None
    assert db.get_run(record.id)["status"] == "CANCELLED"


@pytest.mark.anyio
async def test_stream_accepts_long_lines_and_bad_event_schema(isolated):
    db, _ = isolated
    stream = asyncio.StreamReader()
    stream.feed_data(b"x" * 100000 + b'\n{"type":"test","seq":"bad"}\n')
    stream.feed_data(json.dumps({"type": "test", "seq": 1, "payload": {"ok": True}}).encode() + b"\n")
    stream.feed_eof()
    await JobManager()._stream_reader("run", stream, True)
    assert len(db.get_logs("run")[0]["line"]) == 100000
    assert len(db.get_logs("run")) == 2
    assert db.get_events("run")[0]["payload"] == {"ok": True}


@pytest.mark.anyio
async def test_reader_failure_terminates_process_before_releasing_device(isolated, monkeypatch):
    db, devices = isolated
    from webui.backend.app.commands.registry import get_command_registry
    registry = get_command_registry()
    monkeypatch.setattr(registry, "build_cli_argv", lambda *args: [sys.executable, "-u", "-c",
                        "import time; print('ready'); time.sleep(60)"])
    manager = JobManager()
    async def failed_reader(*args):
        raise RuntimeError("reader failed")
    monkeypatch.setattr(manager, "_handle_line", failed_reader)
    record = await manager.create_and_start_run("live", {})
    await asyncio.wait_for(manager._tasks[record.id], 12)
    assert db.get_run(record.id)["status"] == "FAILED"
    assert devices.get_lock_info("hackrf") is None
    assert not manager._active_processes


@pytest.mark.anyio
async def test_shutdown_cancels_queued_runs(isolated):
    db, devices = isolated
    manager = JobManager()
    # 标记取消发生在任务开始执行之前。
    record = await manager.create_and_start_run("live", {})
    manager._stop_requests.add(record.id)
    await manager.shutdown()
    assert db.get_run(record.id)["status"] == "CANCELLED"
    assert devices.get_lock_info("hackrf") is None
    with pytest.raises(RuntimeError):
        await manager.create_and_start_run("live", {})


@pytest.mark.anyio
@pytest.mark.parametrize('source', ['payload', 'message'])
async def test_infinite_transmit_stops_cooperatively_and_releases_device(isolated, monkeypatch, tmp_path, source):
    """真实子进程运行 repeat=0，仅将射频驱动替换为 FakeAPI。"""
    db, devices = isolated
    from webui.backend.app.jobs import manager as job_module
    original_spawn = asyncio.create_subprocess_exec
    children = []
    script = "\n".join([
        "import sys, json",
        "sys.path.insert(0, 'tests')",
        "from unittest.mock import patch",
        "from hackrf_fake import FakeAPI",
        "from flrc_modem.transmit_cli import main",
        "api = FakeAPI(buffer_size=4096, tx_interval=0.001)",
        "original_start = api.hackrf_start_tx",
        "def start_tx(*args):",
        "    result = original_start(*args)",
        "    print('TX_ACTIVE', flush=True)",
        "    return result",
        "api.hackrf_start_tx = start_tx",
        "with patch('flrc_modem.transmission.session.HackRFLibrary', return_value=api):",
        "    result = main(sys.argv[1:])",
        "print(json.dumps({'fake_calls': api.calls}), flush=True)",
        "raise SystemExit(result)",
    ])
    async def fake_radio_spawn(*argv, **kwargs):
        assert kwargs['env']['FLRC_CONTROL_STDIN'] == '1'
        assert kwargs['stdin'] == asyncio.subprocess.PIPE
        child = await original_spawn(sys.executable, '-u', '-c', script, *argv[4:], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(job_module.asyncio, 'create_subprocess_exec', fake_radio_spawn)
    manager = JobManager()
    monkeypatch.setattr(manager, '_terminate_process', Mock(side_effect=AssertionError('must stop cooperatively')))
    parameters = dict(frequency=2480000000, sync='54455354', repeat=0)
    if source == 'message':
        path = tmp_path / 'message.json'
        path.write_text('{"direction":"ground_to_air","command":"0x23","fields":{"data_hex":""}}')
        parameters.update(message_file=str(path), protocol='dk32s')
    else:
        parameters['payload_hex'] = '000102030405'
    record = await manager.create_and_start_run('transmit', parameters)
    task = manager._tasks[record.id]
    try:
        async def wait_for_device():
            while not any(row['line'] == 'TX_ACTIVE' for row in db.get_logs(record.id)):
                if task.done():
                    pytest.fail(str(db.get_logs(record.id)))
                await asyncio.sleep(.02)
        await asyncio.wait_for(wait_for_device(), 5)
        assert devices.get_lock_info('hackrf') is not None
        assert await asyncio.wait_for(manager.stop_run(record.id), 5)
        assert children[0].returncode == 0
        assert db.get_run(record.id)['status'] == 'CANCELLED'
        assert devices.get_lock_info('hackrf') is None
        lines = [row['line'] for row in db.get_logs(record.id)]
        stats = next(json.loads(line) for line in lines if line.startswith('{"status"'))
        assert stats['status'] == 'cancelled'
        calls = next(json.loads(line)['fake_calls'] for line in lines if line.startswith('{"fake_calls"'))
        assert 'stop_tx' in calls
        assert calls[-2:] == ['close', 'release']
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()
        await asyncio.wait_for(task, 5)


@pytest.mark.anyio
async def test_cancelled_stop_request_does_not_abandon_termination(isolated, monkeypatch):
    db, devices = isolated
    from webui.backend.app.commands.registry import get_command_registry
    monkeypatch.setattr(get_command_registry(), 'build_cli_argv', lambda *args: [
        sys.executable, '-u', '-c', "import time; time.sleep(60)"])
    manager = JobManager()
    original_terminate = manager._terminate_process
    started, proceed = asyncio.Event(), asyncio.Event()
    async def delayed_terminate(proc):
        started.set()
        await proceed.wait()
        await original_terminate(proc)
    monkeypatch.setattr(manager, '_terminate_process', delayed_terminate)
    record = await manager.create_and_start_run('live', {})
    async def wait_for_spawn():
        while record.id not in manager._active_processes:
            await asyncio.sleep(.01)
    await asyncio.wait_for(wait_for_spawn(), 3)
    stopping = asyncio.create_task(manager.stop_run(record.id))
    await asyncio.wait_for(started.wait(), 3)
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert devices.get_lock_info('hackrf') is not None
    # 模拟页面重复点击；两个请求共享尚未完成的停止流程。
    retry = asyncio.create_task(manager.stop_run(record.id))
    proceed.set()
    assert await asyncio.wait_for(retry, 12)
    assert db.get_run(record.id)['status'] == 'CANCELLED'
    assert devices.get_lock_info('hackrf') is None
