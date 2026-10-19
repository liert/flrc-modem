"""收发入口共用的设备选择参数及实际选择结果提示。"""
import sys

from .discovery import describe_device


def add_device_arguments(parser):
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--serial", help="指定设备完整序列号或唯一后缀；失败时不切换设备")
    selection.add_argument("--device-index", type=int,
                           help="指定设备索引（从 0 开始，用 hackrf info 查看）；默认自动选择可打开设备")


def report_selected_device(record):
    if record is not None:
        print("已选择 HackRF：" + describe_device(record), file=sys.stderr, flush=True)
