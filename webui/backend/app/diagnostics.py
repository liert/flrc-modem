"""Keep native fault traces and bounded service logs across abrupt exits."""
from datetime import datetime, timezone
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from .paths import PROJECT_ROOT

_fault_stream = None
_service_handler = None


def enable_fault_log(label: str) -> Path | None:
    global _fault_stream
    path = PROJECT_ROOT / 'outputs' / 'logs' / f'{label}-{os.getpid()}.fault.log'
    if _fault_stream is not None:
        return Path(_fault_stream.name)
    stream = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open('a', encoding='utf-8', buffering=1)
        stream.write(f'\n{datetime.now(timezone.utc).isoformat()} pid={os.getpid()} Python={sys.version}\n')
        stream.flush()
        faulthandler.enable(file=stream, all_threads=True)
        # faulthandler keeps the descriptor, so retain the file for process lifetime.
        _fault_stream = stream
        return path
    except (OSError, RuntimeError) as error:
        if stream is not None:
            stream.close()
        logging.getLogger(__name__).warning('无法启用原生崩溃日志：%s', error)
        return None


def install_service_diagnostics():
    global _service_handler
    path = enable_fault_log('webui')
    if _service_handler is None:
        try:
            log_path = PROJECT_ROOT / 'outputs' / 'logs' / 'webui.log'
            log_path.parent.mkdir(parents=True, exist_ok=True)
            _service_handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024,
                                                  backupCount=3, encoding='utf-8')
            _service_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
            logging.getLogger().addHandler(_service_handler)
        except OSError as error:
            logging.getLogger(__name__).warning('无法写入服务日志：%s', error)
    logging.getLogger(__name__).info('WebUI pid=%s；原生崩溃堆栈：%s', os.getpid(), path or '未启用')
