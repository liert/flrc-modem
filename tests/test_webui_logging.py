"""WebUI 服务和原生子进程共用统一日志系统。"""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock

from flrc_modem.log import get_default_log_dir
from webui.backend.app import diagnostics
from webui.backend.app.devices import probe


def test_probe_failure_reports_canonical_fault_path(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(diagnostics, "get_default_log_dir", lambda: log_dir)
    process = MagicMock()
    process.pid = 1234
    process.returncode = -1073741819
    process.communicate.return_value = (b"", b"")
    process.__enter__.return_value = process
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *a, **k: process)

    info = probe.run_probe("hackrf")
    expected = log_dir / "device-probe-hackrf-1234.fault.log"
    assert str(expected) in info.error_message
    assert diagnostics.fault_log_path("spectrum-hackrf", 1234) == log_dir / "spectrum-hackrf-1234.fault.log"


def test_service_logging_and_fault_monitor_survive_reinitialization(tmp_path):
    # 隔离进程级异常钩子与全局监听器，实际验证文件刷新、监控启用及退出关闭。
    script = '''
import faulthandler
import io
import logging
from pathlib import Path
import sys
from flrc_modem.log import setup_logging, shutdown_logging
from flrc_modem.log import crash
from webui.backend.app import diagnostics

log_dir = Path(sys.argv[1]) / "logs"
diagnostics.get_default_log_dir = lambda: log_dir
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
    shutdown_logging()
    assert stream.closed and not faulthandler.is_enabled()

content = (log_dir / "webui.log").read_text(encoding="utf-8")
for i in range(2):
    for name in ("sync", "service", "worker"):
        assert content.count(f"{name}-marker-{i}") == 1
assert "File" in (log_dir / "crash.log").read_text(encoding="utf-8")
assert not (Path(sys.argv[1]) / "outputs").exists()
'''
    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(repo_root), str(repo_root / "src")))}
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_log_directory_is_project_root():
    assert get_default_log_dir() == Path(__file__).resolve().parents[1] / "logs"
