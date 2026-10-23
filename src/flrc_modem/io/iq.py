"""按窗口读取交错 I/Q，保留重叠区用于跨边界包恢复。"""
from pathlib import Path
from tempfile import NamedTemporaryFile
import numpy as np

from ..config import DemodConfig

SAMPLE_BYTES = {"cs8": 2, "cu8": 2, "cf32": 8}


def encode_samples(iq, iq_format: str) -> bytes:
    """归一化复数转交错 I/Q；整数格式采用就近量化和饱和限制。"""
    if iq_format not in SAMPLE_BYTES:
        raise ValueError("不支持的 IQ 格式")
    samples = np.asarray(iq)
    if samples.ndim != 1 or not np.all(np.isfinite(samples)):
        raise ValueError("IQ 必须是一维有限复数样本")
    values = np.empty((len(samples), 2), dtype=np.float32)
    values[:, 0], values[:, 1] = samples.real, samples.imag
    if iq_format == "cf32":
        if not np.all(np.isfinite(values)):
            raise ValueError("IQ 幅度超过 float32 范围")
        return values.astype("<f4").tobytes()
    # 先裁剪再缩放，避免极大输入在乘法时溢出。
    quantized = np.rint(np.clip(values, -1, 1) * 128)
    if iq_format == "cu8":
        return np.clip(quantized + 128, 0, 255).astype(np.uint8).tobytes()
    return np.clip(quantized, -128, 127).astype(np.int8).tobytes()


def write_iq(path: Path, chunks, iq_format: str) -> int:
    """流式写 IQ，返回复数样本数；调用方负责检查输入输出路径冲突。"""
    if iq_format not in SAMPLE_BYTES:
        raise ValueError("不支持的 IQ 格式")
    count, temporary = 0, None
    try:
        # 写完整后再替换目标；参数错误和生成中断不会破坏已有文件。
        with NamedTemporaryFile(dir=path.parent, prefix=".flrc-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            for chunk in chunks:
                raw = encode_samples(chunk, iq_format)
                stream.write(raw)
                count += len(raw) // SAMPLE_BYTES[iq_format]
        temporary.replace(path)
        return count
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def decode_samples(raw: bytes | memoryview | np.ndarray, iq_format: str) -> np.ndarray:
    iq_format = {"fc32": "cf32", "sc8": "cs8", "sc16": "cs16"}.get(iq_format, iq_format)
    sample_bytes = {**SAMPLE_BYTES, "cs16": 4}
    if iq_format not in sample_bytes:
        raise ValueError("不支持的 IQ 格式")
    if isinstance(raw, np.ndarray) and raw.dtype == np.complex64:
        if raw.ndim != 1 or not np.all(np.isfinite(raw)):
            raise ValueError("IQ 必须是一维有限复数样本")
        return raw
    raw = memoryview(raw)
    if raw.nbytes % sample_bytes[iq_format]:
        raise ValueError("IQ 字节数不是完整 I/Q 采样对")
    if iq_format == "cf32":
        values = np.frombuffer(raw, dtype="<f4")
        if not np.all(np.isfinite(values)):
            raise ValueError("cf32 数据含 NaN/Inf，请检查文件格式")
    elif iq_format == "cs16":
        values = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    else:
        values = np.frombuffer(raw, dtype=np.int8 if iq_format == "cs8" else np.uint8).astype(np.float32)
        if iq_format == "cu8":
            values -= 128
        values /= 128  # 统一缩放；功率字段仅表示样本幅度，不能解释为 dBm。
    return (values[0::2] + 1j * values[1::2]).astype(np.complex64)


def iter_iq_chunks(path: Path, config: DemodConfig):
    sample_bytes = SAMPLE_BYTES[config.iq_format]
    size = path.stat().st_size
    if size % sample_bytes:
        raise ValueError("IQ 文件末尾含不完整采样对")
    if not size:
        raise ValueError("IQ 文件为空")
    total_samples = size // sample_bytes
    if config.max_seconds > 0:
        total_samples = min(total_samples, int(config.max_seconds * config.sample_rate))
    step = max(1, int(round(config.chunk_seconds * config.sample_rate)))
    overlap = int(round(config.overlap_seconds * config.sample_rate))
    with path.open("rb") as stream:
        for start in range(0, total_samples, step):
            count = min(step + overlap, total_samples - start)
            stream.seek(start * sample_bytes)
            raw = stream.read(count * sample_bytes)
            yield start / config.sample_rate, decode_samples(raw, config.iq_format)
