"""Cooperative cancellation shared by scan capture and analysis."""
from threading import Event


class ScanCancelled(Exception):
    """The user requested that the current scan stop."""


def check_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled("扫描已停止")
