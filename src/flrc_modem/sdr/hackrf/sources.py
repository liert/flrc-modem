"""在生产线程中读取/转换 IQ，保持 USB 回调只处理现成 cs8。"""
from pathlib import Path

from ...io.iq import SAMPLE_BYTES, decode_samples, encode_samples


class IQFileSource:
    def __init__(self, path: Path, iq_format="cs8", repeat=1):
        if iq_format not in SAMPLE_BYTES:
            raise ValueError("IQ 格式必须为 cs8、cu8 或 cf32")
        if not isinstance(repeat, int) or repeat < 1:
            raise ValueError("repeat 必须为正整数")
        self.path, self.iq_format, self.repeat = Path(path), iq_format, repeat
        size = self.path.stat().st_size
        if not size or size % SAMPLE_BYTES[iq_format]:
            raise ValueError("IQ 文件为空或含不完整采样对")
        self.sample_count = size // SAMPLE_BYTES[iq_format] * repeat
        self._remaining = repeat
        self._file = None

    def __enter__(self):
        self._file = self.path.open("rb", buffering=0)
        return self

    def __exit__(self, *args):
        self._file.close()

    def readinto(self, destination):
        if self._file is None:
            raise ValueError("请在 with IQFileSource(...) 中使用文件源")
        if len(destination) % 2:
            raise ValueError("目标缓冲必须容纳完整 I/Q 对")
        used = 0
        while used < len(destination) and self._remaining:
            if self.iq_format == "cs8":
                # FileIO.readinto 直接读入预分配的槽位，避免大 bytes 临时对象。
                count = self._file.readinto(destination[used:])
            else:
                raw_count = (len(destination) - used) // 2 * SAMPLE_BYTES[self.iq_format]
                raw = self._file.read(raw_count)
                converted = encode_samples(decode_samples(raw, self.iq_format), "cs8")
                count = len(converted)
                destination[used:used + count] = converted
            if not count:
                self._remaining -= 1
                if self._remaining:
                    self._file.seek(0)
            else:
                used += count
        return used


class IQIterableSource:
    """将复数 NumPy 样本块适配为 readinto；可直接接 modulation.iter_signal。"""
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self._pending = memoryview(b"")

    def readinto(self, destination):
        used = 0
        while used < len(destination):
            if not self._pending:
                try:
                    self._pending = memoryview(encode_samples(next(self.chunks), "cs8"))
                except StopIteration:
                    break
                if not self._pending:
                    continue
            count = min(len(destination) - used, len(self._pending))
            destination[used:used + count] = self._pending[:count]
            self._pending = self._pending[count:]
            used += count
        return used
