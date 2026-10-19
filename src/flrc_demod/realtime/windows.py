"""任意长度 cs8 数据块到重叠窗口；固定缓冲池限制实时积压。"""
from dataclasses import dataclass
import queue
import threading
import time

from ..config import DemodConfig


class RealtimeBacklogError(RuntimeError):
    pass


@dataclass(frozen=True)
class IQWindow:
    slot: int
    start_sample: int
    sample_count: int
    queued_at: float


class WindowQueue:
    def __init__(self, config: DemodConfig, depth: int = 8):
        if not isinstance(depth, int) or not 2 <= depth <= 128:
            raise ValueError("实时窗口队列深度必须为 2..128")
        self.step_bytes = max(1, round(config.chunk_seconds * config.sample_rate)) * 2
        self.overlap_bytes = round(config.overlap_seconds * config.sample_rate) * 2
        self.capacity = self.step_bytes + self.overlap_bytes
        if self.capacity * (depth + 2) > 256 * 1024 * 1024:
            raise ValueError("实时窗口缓冲池不能超过 256MiB")
        self.buffers = [bytearray(self.capacity) for _ in range(depth + 2)]
        self.free = queue.Queue(depth + 2)
        self.ready = queue.Queue(depth)
        for slot in range(depth + 2):
            self.free.put(slot)
        self.current = self.free.get_nowait()
        self.filled = self.start_byte = 0
        self.input_bytes = self.discarded_bytes = self.high_watermark = 0
        self.closed = False
        self.abort = threading.Event()

    def write(self, data):
        if self.closed:
            raise ValueError("实时窗口输入已关闭")
        data = memoryview(data).cast("B")
        if self.abort.is_set():
            self.discarded_bytes += len(data)
            return len(data)
        used = 0
        while used < len(data):
            count = min(len(data) - used, self.capacity - self.filled)
            self.buffers[self.current][self.filled:self.filled + count] = data[used:used + count]
            self.filled += count
            self.input_bytes += count
            used += count
            if self.filled == self.capacity:
                self._publish(final=False)
        return used

    def _publish(self, final):
        window = IQWindow(self.current, self.start_byte // 2, self.filled // 2, time.monotonic())
        if final:
            while not self.abort.is_set():
                try:
                    self.ready.put(window, timeout=0.05)
                    break
                except queue.Full:
                    pass
            else:
                return
        else:
            try:
                self.ready.put_nowait(window)
            except queue.Full as error:
                raise RealtimeBacklogError("实时解调积压超过窗口队列容量，请减少信道/输出开销或调整缓冲") from error
        self.high_watermark = max(self.high_watermark, self.ready.qsize())
        try:
            next_slot = self.free.get_nowait()
        except queue.Empty as error:
            raise RealtimeBacklogError("实时窗口缓冲池已耗尽") from error
        advance = min(self.step_bytes, self.filled)
        retained = self.filled - advance
        # 窗口交出后不能再修改；只把重叠区复制到下一个写槽位。
        if retained:
            self.buffers[next_slot][:retained] = self.buffers[self.current][advance:self.filled]
        self.current, self.filled = next_slot, retained
        self.start_byte += advance

    def finish(self):
        """采集已经停止后补交残余窗口，与离线 EOF 的窗口位置一致。"""
        if self.closed:
            return
        self.closed = True
        if self.filled % 2:
            raise ValueError("实时 IQ 末尾含不完整采样对")
        while self.filled and not self.abort.is_set():
            self._publish(final=True)

    def view(self, window):
        """视图只在 release 前有效，解调必须在归还槽位前完成。"""
        return memoryview(self.buffers[window.slot])[:window.sample_count * 2]

    def release(self, window):
        self.free.put_nowait(window.slot)
