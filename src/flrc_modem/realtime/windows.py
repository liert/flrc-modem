"""实时 IQ 队列与重叠窗口；有界缓冲、槽位所有权和断流边界共用实现。"""
from dataclasses import dataclass
import queue
import threading
import time

from ..config import DemodConfig
from ..io.iq import SAMPLE_BYTES


class RealtimeBacklogError(RuntimeError):
    pass


class IQBlockQueue(queue.Queue):
    """单生产者 IQ 块队列；过载丢最旧块，在首个幸存块标记断流。

    入队和断流标记在同一把锁内完成，消费者不会先取走未标记的幸存块。
    块格式为 (raw_bytes, start_time_s, discontinuity)。
    """

    def __init__(self, depth: int, sample_bytes: int):
        if depth < 1 or sample_bytes < 1:
            raise ValueError("IQ 队列容量和采样字节数必须为正数")
        super().__init__(maxsize=depth)
        self.sample_bytes = sample_bytes
        self.dropped_blocks = self.dropped_samples = 0

    def put_latest(self, data, timestamp, gap=False):
        if len(data) % self.sample_bytes:
            raise ValueError("IQ 数据块含不完整采样对")
        with self.not_full:
            if self._qsize() == self.maxsize:
                raw, _, _ = self._get()
                self.dropped_blocks += 1
                self.dropped_samples += len(raw) // self.sample_bytes
                self.unfinished_tasks -= 1
                if self._qsize():
                    raw, start, _ = self.queue[0]
                    self.queue[0] = (raw, start, True)
                else:
                    gap = True
            self._put((data, timestamp, gap))
            self.unfinished_tasks += 1
            self.not_empty.notify()


@dataclass(frozen=True)
class IQWindow:
    slot: int
    start_sample: int
    sample_count: int
    queued_at: float
    start_time_s: float | None = None
    epoch: int = 0


class WindowQueue:
    def __init__(self, config: DemodConfig, depth: int = 8, *, overflow_policy: str = "error"):
        if not isinstance(depth, int) or not 2 <= depth <= 128:
            raise ValueError("实时窗口队列深度必须为 2..128")
        if overflow_policy not in ("error", "latest"):
            raise ValueError("实时窗口过载策略必须为 error 或 latest")
        self.overflow_policy = overflow_policy
        self.sample_rate = config.sample_rate
        self.sample_bytes = SAMPLE_BYTES[config.iq_format]
        self.step_bytes = max(1, round(config.chunk_seconds * config.sample_rate)) * self.sample_bytes
        self.overlap_bytes = round(config.overlap_seconds * config.sample_rate) * self.sample_bytes
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
        self.dropped_windows = self.dropped_samples = 0
        self.capacity_drops = self.stale_drops = 0
        self.last_dropped_start_sample = self.last_dropped_end_sample = None
        self._drop_lock = threading.Lock()
        self.closed = False
        self.abort = threading.Event()
        self.epoch = self.discontinuities = self.partial_samples_discarded = 0
        self._published_end_byte = 0
        self._time_origin = None

    def mark_discontinuity(self):
        """生产者在下一段 IQ 前调用；丢弃残余与重叠，保留已交出的窗口。"""
        self.partial_samples_discarded += max(0, self.input_bytes - self._published_end_byte) // self.sample_bytes
        self.filled = 0
        self.start_byte = self._published_end_byte = self.input_bytes
        self._time_origin = None
        self.epoch += 1
        self.discontinuities += 1

    def write_block(self, data, start_time_s, *, discontinuity=False):
        """带首样本时间的完整 IQ 块；连续块沿样本时钟推进，不跟随主机调度抖动。"""
        if len(data) % self.sample_bytes:
            raise ValueError("IQ 数据块含不完整采样对")
        if discontinuity:
            self.mark_discontinuity()
        if self._time_origin is None:
            self._time_origin = start_time_s - (self.input_bytes // self.sample_bytes) / self.sample_rate
        return self.write(data)

    def iter_block(self, data, start_time_s, *, discontinuity=False):
        """同线程组窗并逐窗消费，任意大的驱动块不会制造额外排队或丢窗。

        每个 yield 的窗口仍需消费者 release；不要与另一窗口消费者并用。
        """
        data = memoryview(data).cast("B")
        if len(data) % self.sample_bytes:
            raise ValueError("IQ 数据块含不完整采样对")
        self.write_block(b"", start_time_s, discontinuity=discontinuity)
        offset = 0
        while offset < len(data) and not self.abort.is_set():
            end = min(len(data), offset + self.capacity - self.filled)
            self.write(data[offset:end])
            offset = end
            while True:
                try:
                    window = self.ready.get_nowait()
                except queue.Empty:
                    break
                yield window

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
        start_sample = self.start_byte // self.sample_bytes
        start_time = None if self._time_origin is None else self._time_origin + start_sample / self.sample_rate
        window = IQWindow(self.current, start_sample, self.filled // self.sample_bytes,
                          time.monotonic(), start_time, self.epoch)
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
                if self.overflow_policy == "error":
                    raise RealtimeBacklogError("实时解调积压超过窗口队列容量，请减少信道/输出开销或调整缓冲") from error
                # 只回收尚未交给 DSP 的窗口；在用的槽位仍由消费者释放。
                # 清理陈旧待办后追赶当前数据，而不是无限增加处理延迟。
                while True:
                    try:
                        stale = self.ready.get_nowait()
                    except queue.Empty:
                        break
                    self.discard(stale)
                self.ready.put_nowait(window)
        self.high_watermark = max(self.high_watermark, self.ready.qsize())
        self._published_end_byte = max(self._published_end_byte, self.start_byte + self.filled)
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
        if self.filled % self.sample_bytes:
            raise ValueError("实时 IQ 末尾含不完整采样对")
        while self.filled and not self.abort.is_set():
            self._publish(final=True)

    def view(self, window):
        """视图只在 release 前有效，解调必须在归还槽位前完成。"""
        return memoryview(self.buffers[window.slot])[:window.sample_count * self.sample_bytes]

    def release(self, window):
        self.free.put_nowait(window.slot)

    def discard(self, window, *, reason="capacity"):
        """显式跳过未解调窗口，保留原始采样索引；重叠区不重复计数。"""
        with self._drop_lock:
            self.dropped_windows += 1
            skipped = min(window.sample_count, self.step_bytes // self.sample_bytes)
            self.dropped_samples += skipped
            if reason == "stale":
                self.stale_drops += 1
            else:
                self.capacity_drops += 1
            self.last_dropped_start_sample = window.start_sample
            self.last_dropped_end_sample = window.start_sample + skipped
        self.release(window)

    def drop_metrics(self):
        with self._drop_lock:
            return {"windows_skipped": self.dropped_windows, "skipped_samples": self.dropped_samples,
                    "queue_full_drops": self.capacity_drops, "stale_window_drops": self.stale_drops,
                    "discontinuities": self.discontinuities,
                    "partial_samples_discarded": self.partial_samples_discarded,
                    "last_skipped_start_sample": self.last_dropped_start_sample,
                    "last_skipped_end_sample": self.last_dropped_end_sample}
