"""Keep native fault traces and bounded service logs across abrupt exits."""
import logging
import os
from pathlib import Path

from flrc_modem.log import get_default_log_dir
from flrc_modem.log.crash import enable_native_fault_monitor


def fault_log_path(label: str, pid: int | None = None) -> Path:
    """Return the canonical path for a process-native fault log."""
    process_id = os.getpid() if pid is None else pid
    return get_default_log_dir() / f'{label}-{process_id}.fault.log'


def enable_fault_log(label: str) -> Path | None:
    return enable_native_fault_monitor(fault_log_path(label))


def install_service_diagnostics():
    path = enable_fault_log('webui')
    # 服务日志由 flrc_modem.log.setup_logging() 统一写入 logs/webui.log。
    # 这里仅登记原生崩溃文件，避免再次给 root logger 挂载第二个文件 handler。
    logging.getLogger('flrc_webui').info('WebUI pid=%s；原生崩溃堆栈：%s', os.getpid(), path or '未启用')
