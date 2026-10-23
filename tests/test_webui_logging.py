"""WebUI 服务和原生子进程共用统一日志系统。"""
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import MagicMock

import pytest

from flrc_modem.log import get_default_log_dir
from webui.backend.app import diagnostics
from webui.backend.app.devices import probe
from webui.backend.app.schemas import DeviceInfo, DeviceStatus


@pytest.mark.parametrize("failure", ["crash", "timeout", "invalid_result"])
def test_probe_failure_uses_service_log(tmp_path, monkeypatch, caplog, failure):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(probe, "get_default_log_dir", lambda: log_dir)
    process = MagicMock()
    process.pid = 1234
    process.returncode = -1073741819 if failure == "crash" else 0
    stderr = b"x" * 70000 + b"\nWindows fatal exception: access violation\nFile driver.py, line 1"
    process.communicate.return_value = (b"", stderr)
    if failure == "timeout":
        process.communicate.side_effect = [subprocess.TimeoutExpired("probe", 10), (b"", stderr)]
    process.__enter__.return_value = process
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *a, **k: process)

    info = probe.run_probe("hackrf")
    assert info.status == DeviceStatus.ERROR
    if failure != "invalid_result":
        assert str(log_dir / "webui.log") in info.error_message
    events = [record.msg for record in caplog.records if isinstance(record.msg, dict)]
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "device.probe.failed"
    assert event["device_type"] == "hackrf" and event["pid"] == 1234
    assert event["stderr"] == stderr.decode()[-65536:]
    assert event["stderr_truncated"] is True
    assert event["failure"] == {"crash": "process_exit", "timeout": "timeout", "invalid_result": "invalid_result"}[failure]
    if failure == "crash":
        assert event["exit_code_hex"] == "0xC0000005"
    if failure == "timeout":
        process.kill.assert_called_once()
    assert not list(tmp_path.rglob("*.fault.log"))


@pytest.mark.parametrize("status", [DeviceStatus.ONLINE, DeviceStatus.OFFLINE, DeviceStatus.ERROR])
def test_probe_valid_result_does_not_create_fault_log(tmp_path, monkeypatch, caplog, status):
    monkeypatch.setattr(probe, "get_default_log_dir", lambda: tmp_path / "logs")
    expected = DeviceInfo(device_type="usrp", id="test", name="test", status=status)
    process = MagicMock()
    process.returncode = 0
    process.communicate.return_value = (
        ("driver message\n" + probe._RESULT_PREFIX + expected.model_dump_json() + "\n").encode(), b"",
    )
    process.__enter__.return_value = process
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *a, **k: process)
    assert probe.run_probe("usrp") == expected
    assert not caplog.records
    assert not list(tmp_path.rglob("*.fault.log"))


def test_cleanup_only_removes_old_header_only_probe_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "get_default_log_dir", lambda: tmp_path)
    header = "\n=== [Native Fault Monitor] Started at 2026-10-23T06:04:23+00:00 pid=123 python=3.14.6 ===\n"
    contents = {
        "device-probe-usrp-1.fault.log": "",
        "device-probe-hackrf-2.fault.log": header,
        "device-probe-usrp-3.fault.log": header * 2,
        "device-probe-hackrf-4.fault.log": header + "Windows fatal exception: access violation\nFile driver.py, line 1",
        "device-probe-usrp-5.fault.log": header,  # recent file
        "device-probe-other-6.fault.log": header,
        "spectrum-hackrf-7.fault.log": header,
        "device-probe-usrp-8.fault.log": header * 100,
    }
    for name, content in contents.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        if name != "device-probe-usrp-5.fault.log":
            os.utime(path, (time.time() - 120, time.time() - 120))
    assert diagnostics.cleanup_probe_fault_logs() == 3
    assert {path.name for path in tmp_path.iterdir()} == set(contents) - {
        "device-probe-usrp-1.fault.log", "device-probe-hackrf-2.fault.log", "device-probe-usrp-3.fault.log",
    }
    assert diagnostics.cleanup_probe_fault_logs() == 0


def test_probe_child_sends_native_trace_to_stderr_without_fault_file(tmp_path):
    script = '''
import faulthandler
import sys
from webui.backend.app.devices import probe
from webui.backend.app.schemas import DeviceInfo, DeviceStatus

def fake_probe():
    assert faulthandler.is_enabled()
    faulthandler.dump_traceback()
    return DeviceInfo(device_type="usrp", id="test", name="test", status=DeviceStatus.OFFLINE)

probe.probe_usrp = fake_probe
sys.argv = ["probe", "usrp"]
raise SystemExit(probe.main())
'''
    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(repo_root), str(repo_root / "src")))}
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert probe._RESULT_PREFIX in result.stdout
    assert "fake_probe" in result.stderr
    assert not list(tmp_path.rglob("*.fault.log"))


def test_service_logging_and_fault_monitor_survive_reinitialization(tmp_path):
    # 隔离进程级异常钩子与全局监听器，实际验证文件刷新、监控启用及退出关闭。
    script = '''
import faulthandler
import io
import json
import logging
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch
from flrc_modem.log import setup_logging, shutdown_logging
from flrc_modem.log import crash
from webui.backend.app import diagnostics
from webui.backend.app.devices import probe

log_dir = Path(sys.argv[1]) / "logs"
diagnostics.get_default_log_dir = lambda: log_dir
probe.get_default_log_dir = lambda: log_dir
root_handlers = list(logging.getLogger().handlers)
for i in range(2):
    setup_logging(file_path=log_dir / "webui.log", stream=io.StringIO(),
                  extra_loggers=["flrc_webui", "webui"])
    assert faulthandler.is_enabled()
    stream = crash._FAULT_STREAM
    assert stream is not None and not stream.closed
    diagnostics.install_service_diagnostics()
    assert logging.getLogger().handlers == root_handlers
    assert diagnostics.enable_fault_log("webui") == log_dir / "crash.log"
    assert crash._FAULT_STREAM is stream
    faulthandler.dump_traceback(file=stream)
    logging.getLogger("flrc_modem.sync").info("sync-marker-%s", i)
    logging.getLogger("flrc_webui").info("service-marker-%s", i)
    logging.getLogger("webui.backend.app.spectrum.session").info("worker-marker-%s", i)
    if i == 0:
        process = MagicMock()
        process.pid = 1234
        process.returncode = -1073741819
        process.communicate.return_value = (b"", b"native-stack-marker")
        process.__enter__.return_value = process
        with patch.object(probe.subprocess, "Popen", return_value=process):
            probe.run_probe("hackrf")
    shutdown_logging()
    assert stream.closed and not faulthandler.is_enabled()

content = (log_dir / "webui.log").read_text(encoding="utf-8")
for i in range(2):
    for name in ("sync", "service", "worker"):
        assert content.count(f"{name}-marker-{i}") == 1
assert "File" in (log_dir / "crash.log").read_text(encoding="utf-8")
failures = [json.loads(line[line.index("{"):]) for line in content.splitlines()
            if "[device.probe.failed]" in line]
assert len(failures) == 1
assert failures[0]["stderr"] == "native-stack-marker"
assert failures[0]["pid"] == 1234 and failures[0]["exit_code_hex"] == "0xC0000005"
assert not list(log_dir.glob("device-probe-*.fault.log"))
assert not (Path(sys.argv[1]) / "outputs").exists()
'''
    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(repo_root), str(repo_root / "src")))}
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_log_directory_is_project_root():
    assert get_default_log_dir() == Path(__file__).resolve().parents[1] / "logs"
