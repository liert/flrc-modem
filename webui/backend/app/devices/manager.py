"""设备管理模块：管理 USRP 与 HackRF 硬件探测、在线状态检查与跨任务互斥控制。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sys
import threading
from typing import Any

from ..schemas import DeviceInfo, DeviceStatus


class DeviceManager:
    """管理 SDR 硬件资源生命周期与互斥锁。"""

    def __init__(self):
        self._lock = threading.Lock()
        # 记录各设备当前持有者: sdr_key -> {"run_id": str, "acquired_at": str}
        self._locks: dict[str, dict[str, Any]] = {}

    def acquire_device(self, sdr_type: str, run_id: str) -> tuple[bool, str | None]:
        """为任务申请指定 SDR 设备的互斥使用权。"""
        sdr_key = sdr_type.lower()
        with self._lock:
            existing = self._locks.get(sdr_key)
            if existing and existing["run_id"] != run_id:
                return (
                    False,
                    f"设备 '{sdr_type.upper()}' 当前已被活动任务 '{existing['run_id']}' 独占锁定，请等待其执行完成或手动终止。",
                )
            self._locks[sdr_key] = {
                "run_id": run_id,
                "acquired_at": datetime.now(timezone.utc).isoformat(),
            }
            return True, None

    def release_device(self, run_id: str) -> list[str]:
        """任务结束或中止时，释放该任务占用的所有设备。"""
        released = []
        with self._lock:
            for sdr_key, info in list(self._locks.items()):
                if info["run_id"] == run_id:
                    del self._locks[sdr_key]
                    released.append(sdr_key)
        return released

    def get_lock_info(self, sdr_type: str) -> dict[str, Any] | None:
        with self._lock:
            return self._locks.get(sdr_type.lower())

    def list_devices(self) -> list[DeviceInfo]:
        """探测并返回当前物理设备或驱动状态。"""
        devices: list[DeviceInfo] = []

        # 1. 探测 USRP
        usrp_info = self._probe_usrp()
        devices.append(usrp_info)

        # 2. 探测 HackRF
        hackrf_info = self._probe_hackrf()
        devices.append(hackrf_info)

        return devices

    def _probe_usrp(self) -> DeviceInfo:
        lock_info = self.get_lock_info("usrp")
        try:
            from flrc_modem.sdr.usrp.discovery import discover_usrp_devices
            from flrc_modem.sdr.usrp.library import UHDError, UHDLibrary

            lib = UHDLibrary()
            found = discover_usrp_devices(lib)
            if found:
                first = found[0]
                status = DeviceStatus.BUSY if lock_info else DeviceStatus.ONLINE
                return DeviceInfo(
                    device_type="usrp",
                    id=first.get("addr", "usrp_n310"),
                    name=f"USRP ({first.get('product', first.get('type', 'N310'))})",
                    details=first,
                    status=status,
                    locked_by_run_id=lock_info["run_id"] if lock_info else None,
                )
            else:
                return DeviceInfo(
                    device_type="usrp",
                    id="usrp_none",
                    name="USRP SDR (未连接硬件)",
                    details={"driver": "UHD.dll 可用", "count": 0},
                    status=DeviceStatus.BUSY if lock_info else DeviceStatus.OFFLINE,
                    locked_by_run_id=lock_info["run_id"] if lock_info else None,
                    error_message="UHD 驱动正常，但未在局域网/USB 发现就绪的 USRP 设备",
                )
        except Exception as err:
            return DeviceInfo(
                device_type="usrp",
                id="usrp_unavailable",
                name="USRP SDR (UHD 离线)",
                details={"driver": "不可用"},
                status=DeviceStatus.OFFLINE,
                locked_by_run_id=lock_info["run_id"] if lock_info else None,
                error_message=f"未找到可用 uhd.dll 或驱动初始化失败: {err}",
            )

    def _probe_hackrf(self) -> DeviceInfo:
        lock_info = self.get_lock_info("hackrf")
        try:
            from flrc_modem.sdr.hackrf.library import HackRFLibrary

            lib = HackRFLibrary()
            serials = lib.probe_devices()
            if serials:
                status = DeviceStatus.BUSY if lock_info else DeviceStatus.ONLINE
                return DeviceInfo(
                    device_type="hackrf",
                    id=serials[0],
                    name="HackRF One",
                    details={"serials": serials, "count": len(serials)},
                    status=status,
                    locked_by_run_id=lock_info["run_id"] if lock_info else None,
                )
            else:
                return DeviceInfo(
                    device_type="hackrf",
                    id="hackrf_none",
                    name="HackRF One (未插入设备)",
                    details={"driver": "内置 libhackrf.dll 正常", "count": 0},
                    status=DeviceStatus.BUSY if lock_info else DeviceStatus.OFFLINE,
                    locked_by_run_id=lock_info["run_id"] if lock_info else None,
                    error_message="未检测到 USB HackRF 设备",
                )
        except Exception as err:
            return DeviceInfo(
                device_type="hackrf",
                id="hackrf_err",
                name="HackRF One (驱动异常)",
                details={},
                status=DeviceStatus.ERROR,
                locked_by_run_id=lock_info["run_id"] if lock_info else None,
                error_message=str(err),
            )


_DEVICE_MGR: DeviceManager | None = None


def get_device_manager() -> DeviceManager:
    global _DEVICE_MGR
    if _DEVICE_MGR is None:
        _DEVICE_MGR = DeviceManager()
    return _DEVICE_MGR
