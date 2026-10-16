"""规则引擎可以实现同一接口，核心解调无需了解规则或具体协议。"""
from collections.abc import Mapping
from importlib import import_module
from typing import Protocol

from ..models import DecodedFrame
from .protocols import BUILTIN_PARSERS


class FrameParser(Protocol):
    def __call__(self, frame: DecodedFrame) -> Mapping | None:
        """返回附加字段；返回 None 表示此解析器不处理该帧。"""
        ...


def load_parser(spec: str | None) -> FrameParser | None:
    if spec is None:
        return None
    spec = BUILTIN_PARSERS.get(spec, spec)
    module_name, separator, object_name = spec.partition(":")
    if not separator or not module_name or not object_name:
        raise ValueError("解析插件格式应为 Python模块:函数名")
    parser = getattr(import_module(module_name), object_name)
    if not callable(parser):
        raise ValueError("解析插件必须可调用")
    return parser
