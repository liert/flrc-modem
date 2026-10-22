"""统一 flrc 与 python -m flrc_modem 入口，按需导入各功能模块。"""
import argparse
from importlib import import_module
from pathlib import Path
import sys

from . import __version__

# 确保无论从何处调用 flrc.exe，项目根目录均在 sys.path 中，以便直接加载 webui 模块
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

COMMANDS = {
    "rx": "flrc_modem.rx_cli",
    "tx": "flrc_modem.tx_cli",
    "demod": "flrc_modem.cli",
    "modulate": "flrc_modem.modulate_cli",
    "live": "flrc_modem.live_cli",
    "transmit": "flrc_modem.transmit_cli",
    "exchange": "flrc_modem.exchange_cli",
    "sync": "flrc_modem.sync_cli",
    "duplex": "flrc_modem.duplex_cli",
    "hackrf": "flrc_modem.sdr.hackrf.cli",
    "usrp": "flrc_modem.sdr.usrp.cli",
    "scan": "flrc_modem.scan_cli",
    "webui": "webui.backend.app.main",
}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in COMMANDS:
        try:
            return import_module(COMMANDS[args[0]]).main(args[1:])
        except ModuleNotFoundError as err:
            # 优雅提示缺少相关依赖
            if "fastapi" in str(err) or "uvicorn" in str(err) or "webui" in str(err):
                print(
                    f"错误: 启动 WebUI 失败 ({err})。\n"
                    f"请确保已在当前环境中安装 WebUI 后端依赖:\n"
                    f"  pip install fastapi uvicorn websockets httpx",
                    file=sys.stderr,
                )
                return 1
            raise
    if args in ([], ["-h"], ["--help"], ["--version"]):
        parser = argparse.ArgumentParser(
            prog="flrc", description="FLRC Modem：调制解调、实时收发与协议插件",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="子命令：\n"
                   "  webui        启动本地专业 WebUI 交互工作台\n"
                   "  rx           统一 IQ 采样采集（支持 --sdr usrp/hackrf）\n"
                   "  tx           统一 IQ 文件发射（支持 --sdr usrp/hackrf）\n"
                   "  demod        离线解调（省略子命令时的默认功能）\n"
                   "  modulate     将载荷调制为 IQ 文件\n"
                   "  live         HackRF 实时接收与解调\n"
                   "  transmit     直接调制并实时发送报文\n"
                   "  exchange     双 HackRF 连续采集及有限发送诊断\n"
                   "  sync         实时 FHSS 跳频与 TDD 硬件时间戳同步分析（支持 --sdr usrp/hackrf）\n"
                   "  duplex       交互式 TDD 同步双工收发机（支持 --sdr usrp/hackrf）\n"
                   "  scan         2.4GHz 全频段自动盲扫与信道感知（离线 IQ / HackRF / USRP）\n"
                   "  usrp         USRP (UHD) 底层原生诊断及专用维护指令\n"
                   "  hackrf       HackRF 底层原生诊断及专用维护指令\n\n"
                   "使用 flrc <子命令> --help 查看具体参数；也可使用 python -m flrc_modem。",
        )
        parser.add_argument("--version", action="version", version=f"flrc-modem {__version__}")
        if args == ["--version"]:
            parser.parse_args(args)
        parser.print_help()
        return 0
    # 保留省略 demod 的参数形式：flrc --iq capture.iq ...。
    return import_module(COMMANDS["demod"]).main(args)
