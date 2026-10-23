"""Keep native fault traces and bounded service logs across abrupt exits."""
import logging
import os
from pathlib import Path
import re
import time

from flrc_modem.log import get_default_log_dir
from flrc_modem.log.crash import enable_native_fault_monitor


def fault_log_path(label: str, pid: int | None = None) -> Path:
    """Return the canonical path for a process-native fault log."""
    process_id = os.getpid() if pid is None else pid
    return get_default_log_dir() / f'{label}-{process_id}.fault.log'


def enable_fault_log(label: str) -> Path | None:
    return enable_native_fault_monitor(fault_log_path(label))


def cleanup_probe_fault_logs() -> int:
    """Remove old header-only probe logs; retain all actual fault evidence.

    Probes are killed after 10 seconds. A 60-second grace period also avoids
    touching files a previous service instance may still be writing.
    """
    removed = 0
    cutoff = time.time() - 60
    header = re.compile(r'(?:\s*=== \[Native Fault Monitor\] Started at \S+ pid=\d+ python=\S+ ===\s*)*')
    for path in get_default_log_dir().glob('device-probe-*.fault.log'):
        if not re.fullmatch(r'device-probe-(?:usrp|hackrf)-\d+\.fault\.log', path.name) or path.is_symlink():
            continue
        try:
            before = path.stat()
            if not path.is_file() or before.st_mtime > cutoff or before.st_size > 4096:
                continue
            content = path.read_text(encoding='utf-8')
            if not header.fullmatch(content.strip()):
                continue
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                continue
            path.unlink()
            removed += 1
        except (OSError, UnicodeError):
            # Locked, changed, unreadable, or already cleaned by another process.
            continue
    return removed


def install_service_diagnostics():
    removed = cleanup_probe_fault_logs()
    path = enable_fault_log('webui')
    # 服务日志由 flrc_modem.log.setup_logging() 统一写入 logs/webui.log。
    # 这里仅登记原生崩溃文件，避免再次给 root logger 挂载第二个文件 handler。
    logging.getLogger('flrc_webui').info('WebUI pid=%s；原生崩溃堆栈：%s；清理旧探测空日志 %s 个',
                                       os.getpid(), path or '未启用', removed)
