"""发送插件契约：纯函数接收业务字段和样本时钟，返回完整物理载荷。"""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol

from .rules import apply_updates


@dataclass(frozen=True)
class EncodeContext:
    # frame_index 是本次发送流的全局包号，跨不同模板继续递增。
    frame_index: int
    start_sample: int
    sample_rate: float

    @property
    def time_seconds(self) -> float:
        """计划前导起点的样本时钟；不是 USB 回调时间或设备绝对时间。"""
        return self.start_sample / self.sample_rate


class FrameEncoder(Protocol):
    def __call__(self, message: Mapping, context: EncodeContext) -> bytes:
        """不可依赖可变内部状态；启动前预览也会调用本函数。"""
        ...


def load_encoder(spec: str) -> FrameEncoder:
    spec = {"dk32s": "flrc_demod.encoding.dk32s:encode"}.get(spec, spec)
    module, separator, name = spec.partition(":")
    if not separator or not module or not name:
        raise ValueError("发送插件应为 dk32s 或 Python模块:函数名")
    try:
        encoder = getattr(import_module(module), name)
    except (ImportError, AttributeError) as error:
        raise ValueError(f"不能加载发送插件 {spec}：{error}") from error
    if not callable(encoder):
        raise ValueError("发送插件必须可调用")
    return encoder


class MessageTemplate:
    def __init__(self, encoder: FrameEncoder, message: Mapping):
        if not isinstance(message, Mapping):
            raise ValueError("message 必须为 JSON 对象")
        self.encoder = encoder
        # 隔离调用者与插件的修改，避免入队后模板被业务线程意外改写。
        self._message = deepcopy(dict(message))

    def encode(self, context: EncodeContext) -> bytes:
        message = deepcopy(self._message)
        updates = message.pop("updates", {})
        apply_updates(message, updates, context)
        try:
            payload = self.encoder(message, context)
        except Exception as error:
            raise ValueError(f"发送插件编码失败（包号 {context.frame_index}）：{error}") from error
        if not isinstance(payload, bytes):
            raise ValueError("发送插件必须返回 bytes 类型的完整物理载荷")
        if not 6 <= len(payload) <= 127:
            raise ValueError("发送插件返回的物理载荷必须为 6..127B")
        return payload
