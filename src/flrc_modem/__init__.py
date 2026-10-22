"""FLRC Modem：调制解调、实时收发与独立协议插件。"""

__version__ = "0.1.0"

from .log import (
    WideEvent,
    bind_context,
    clear_context,
    get_current_context,
    get_logger,
    setup_logging,
    shutdown_logging,
    wide_event,
)

__all__ = [
    "__version__",
    "get_logger",
    "setup_logging",
    "shutdown_logging",
    "bind_context",
    "get_current_context",
    "clear_context",
    "WideEvent",
    "wide_event",
]
