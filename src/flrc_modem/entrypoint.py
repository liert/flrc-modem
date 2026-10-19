"""统一 flrc 与 python -m flrc_modem 入口，按需导入各功能模块。"""
import argparse
from importlib import import_module
import sys

from . import __version__

COMMANDS = {
    "demod": "flrc_modem.cli",
    "modulate": "flrc_modem.modulate_cli",
    "live": "flrc_modem.live_cli",
    "transmit": "flrc_modem.transmit_cli",
    "hackrf": "flrc_modem.sdr.hackrf.cli",
}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in COMMANDS:
        return import_module(COMMANDS[args[0]]).main(args[1:])
    if args in ([], ["-h"], ["--help"], ["--version"]):
        parser = argparse.ArgumentParser(
            prog="flrc", description="FLRC Modem：调制解调、实时收发与协议插件",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="子命令：\n  demod      离线解调（省略子命令时的默认功能）\n"
                   "  modulate   将载荷调制为 IQ 文件\n  live       HackRF 实时接收与解调\n"
                   "  transmit   HackRF 实时发送\n  hackrf     设备信息及原始 IQ 收发\n\n"
                   "使用 flrc <子命令> --help 查看具体参数；也可使用 python -m flrc_modem。",
        )
        parser.add_argument("--version", action="version", version=f"flrc-modem {__version__}")
        if args == ["--version"]:
            parser.parse_args(args)
        parser.print_help()
        return 0
    # 保留省略 demod 的参数形式：flrc --iq capture.iq ...。
    return import_module(COMMANDS["demod"]).main(args)
