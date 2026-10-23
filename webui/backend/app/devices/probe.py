"""Run native device discovery outside the WebUI process, one driver per child."""
from __future__ import annotations

import logging
import os
import subprocess
import sys

from ..diagnostics import fault_log_path
from ..paths import PROJECT_ROOT
from ..schemas import DeviceInfo, DeviceStatus

logger = logging.getLogger(__name__)
_PROBE_TIMEOUT = 10
_RESULT_PREFIX = 'FLRC_DEVICE_PROBE_RESULT='


def run_probe(kind: str) -> DeviceInfo:
    if kind not in ('usrp', 'hackrf'):
        raise ValueError(f'Unknown SDR kind: {kind}')
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1', 'PYTHONFAULTHANDLER': '1'}
    # The backend is loaded from the checkout, including for flrc.exe launched elsewhere.
    env['PYTHONPATH'] = os.pathsep.join([str(PROJECT_ROOT), str(PROJECT_ROOT / 'src'),
                                       env.get('PYTHONPATH', '')])
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    try:
        with subprocess.Popen(
            [sys.executable, '-m', 'webui.backend.app.devices.probe', kind],
            cwd=str(PROJECT_ROOT), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options,
        ) as proc:
            fault_path = fault_log_path(f'device-probe-{kind}', proc.pid)
            try:
                stdout, stderr = proc.communicate(timeout=_PROBE_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise RuntimeError(f'设备探测超过 {_PROBE_TIMEOUT} 秒，已结束探测进程；日志：{fault_path}') from None
            if proc.returncode:
                code = proc.returncode & 0xFFFFFFFF
                raise RuntimeError(f'设备探测进程异常退出：{proc.returncode} (0x{code:08X})；日志：{fault_path}')
            # Native drivers may print diagnostics to stdout; only consume our marked JSON line.
            for line in reversed(stdout.decode('utf-8', errors='replace').splitlines()):
                if line.startswith(_RESULT_PREFIX):
                    info = DeviceInfo.model_validate_json(line[len(_RESULT_PREFIX):])
                    if info.device_type != kind:
                        raise ValueError('设备探测返回了错误的设备类型')
                    # Successful polling must not accumulate thousands of header-only files.
                    try:
                        fault_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    return info
            detail = stderr.decode('utf-8', errors='replace').strip()[-1000:]
            raise RuntimeError(f'设备探测未返回有效结果：{detail}')
    except (OSError, RuntimeError, ValueError) as error:
        logger.warning('%s 设备探测失败：%s', kind.upper(), error)
        return DeviceInfo(device_type=kind, id=f'{kind}_probe_error', name=f'{kind.upper()} (探测异常)',
                          status=DeviceStatus.ERROR, error_message=str(error))


def probe_usrp() -> DeviceInfo:
    try:
        from flrc_modem.sdr.usrp.discovery import discover_usrp_devices
        from flrc_modem.sdr.usrp.library import UHDLibrary

        lib = UHDLibrary()
        found = discover_usrp_devices(lib)
        if found:
            first = found[0]
            status = DeviceStatus.ONLINE
            return DeviceInfo(
                device_type="usrp",
                id=first.get("addr", "usrp_n310"),
                name=f"USRP ({first.get('product', first.get('type', 'N310'))})",
                details={**first, "devices": found, "count": len(found)},
                status=status,
            )
        else:
            return DeviceInfo(
                device_type="usrp",
                id="usrp_none",
                name="USRP SDR (未连接硬件)",
                details={"driver": "UHD.dll 可用", "count": 0},
                status=DeviceStatus.OFFLINE,
                error_message="UHD 驱动正常，但未在局域网/USB 发现就绪的 USRP 设备",
            )
    except Exception as err:
        return DeviceInfo(
            device_type="usrp",
            id="usrp_unavailable",
            name="USRP SDR (UHD 离线)",
            details={"driver": "不可用"},
            status=DeviceStatus.OFFLINE,
            error_message=f"未找到可用 uhd.dll 或驱动初始化失败: {err}",
        )

def probe_hackrf() -> DeviceInfo:
    try:
        from flrc_modem.sdr.hackrf.library import HackRFLibrary
        from flrc_modem.sdr.hackrf.discovery import device_listing, device_records

        lib = HackRFLibrary()
        lib.acquire()
        try:
            with device_listing(lib) as listing:
                serials = [record["serial"] for record in device_records(listing)]
        finally:
            lib.release()
        if serials:
            status = DeviceStatus.ONLINE
            return DeviceInfo(
                device_type="hackrf",
                id=serials[0],
                name="HackRF One",
                details={"serials": serials, "count": len(serials)},
                status=status,
            )
        else:
            return DeviceInfo(
                device_type="hackrf",
                id="hackrf_none",
                name="HackRF One (未插入设备)",
                details={"driver": "内置 libhackrf.dll 正常", "count": 0},
                status=DeviceStatus.OFFLINE,
                error_message="未检测到 USB HackRF 设备",
            )
    except Exception as err:
        return DeviceInfo(
            device_type="hackrf",
            id="hackrf_err",
            name="HackRF One (驱动异常)",
            details={},
            status=DeviceStatus.ERROR,
            error_message=str(err),
        )


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ('usrp', 'hackrf'):
        return 2
    kind = sys.argv[1]
    from ..diagnostics import enable_fault_log
    enable_fault_log(f'device-probe-{kind}')
    info = probe_usrp() if kind == 'usrp' else probe_hackrf()
    print(_RESULT_PREFIX + info.model_dump_json(), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
