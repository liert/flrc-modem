"""离线信号合成，独立于设备协议解析及文件写入。"""
from .config import ModulationConfig
from .waveform import iter_signal, modulate_bits

__all__ = ["ModulationConfig", "iter_signal", "modulate_bits"]
