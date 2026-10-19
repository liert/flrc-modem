"""固定缓冲池 + 有界队列；USB 回调只复制内存，不读文件、不运行 DSP。"""
import ctypes as C
from dataclasses import asdict, dataclass
from math import isfinite
import queue
import sys
import threading
import time

from .abi import CompleteCallback, FlushCallback, SampleCallback
from .library import HackRFError


class StreamUnderrun(HackRFError):
    pass


class StreamOverrun(HackRFError):
    pass


class StreamCancelled(HackRFError):
    pass


@dataclass
class StreamStats:
    mode: str
    source_bytes: int = 0
    submitted_bytes: int = 0
    completed_bytes: int = 0
    written_bytes: int = 0
    zero_fill_bytes: int = 0
    callbacks: int = 0
    flush_complete: bool = False
    elapsed_seconds: float = 0.0
    device_state: dict | None = None
    device_state_error: str | None = None

    def to_dict(self):
        return asdict(self)


class _Buffer:
    def __init__(self, size):
        self.storage = (C.c_uint8 * size)()
        self.view = memoryview(self.storage).cast("B")
        self.address = C.addressof(self.storage)


class _Stream:
    def __init__(self, api, handle, *, queue_depth=8, block_bytes=None, timeout=30.0, cancel_event=None,
                 health_check=None, health_interval=1.0):
        native_size = int(api.hackrf_get_transfer_buffer_size(handle))
        block_bytes = native_size if block_bytes is None else block_bytes
        if not isinstance(queue_depth, int) or not 2 <= queue_depth <= 256:
            raise ValueError("queue_depth 必须为 2..256")
        if not isinstance(block_bytes, int) or block_bytes < 512 or block_bytes % 512:
            raise ValueError("block_bytes 必须为不小于 512 的 512 倍数")
        if native_size <= 0 or native_size > 16 * 1024 * 1024:
            raise ValueError("DLL 返回的 USB 缓冲大小无效")
        if block_bytes * queue_depth > 256 * 1024 * 1024:
            raise ValueError("缓冲池不能超过 256MiB")
        if timeout is not None and (not isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout 必须为有限正数或 None")
        if not isfinite(health_interval) or health_interval <= 0:
            raise ValueError("health_interval 必须为有限正数")
        if health_check is not None and not callable(health_check):
            raise ValueError("health_check 必须可调用")
        self.api, self.handle = api, handle
        self.native_size, self.block_bytes = native_size, block_bytes
        self.timeout, self.cancel_event = timeout, cancel_event
        self.health_check, self.health_interval = health_check, health_interval
        self.buffers = [_Buffer(block_bytes) for _ in range(queue_depth)]
        self.free = queue.Queue(queue_depth)
        self.ready = queue.Queue(queue_depth + 1)
        for index in range(queue_depth):
            self.free.put(index)
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.error = None
        self.stopped = True

    def _fail(self, error):
        if self.error is None:
            self.error = error
        self.finished.set()

    def _free_buffer(self):
        while not self.cancel.is_set():
            try:
                return self.free.get(timeout=0.05)
            except queue.Empty:
                pass
        return None

    def _wait(self):
        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        next_health = time.monotonic() + self.health_interval
        while not self.finished.wait(0.05):
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise StreamCancelled("用户取消流式操作")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("HackRF 流式操作超时")
            if self.health_check is not None and time.monotonic() >= next_health:
                self.health_check()
                next_health = time.monotonic() + self.health_interval
        if self.error is not None:
            raise self.error

    def _stop(self, operation):
        primary = sys.exception()
        try:
            self.api.check(getattr(self.api, operation)(self.handle), operation)
            self.stopped = True
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note(f"停止设备时另有错误：{error}")


class TxStream(_Stream):
    def __init__(self, api, handle, source, *, prefill_blocks=None, prefill_timeout=10.0,
                 underrun_policy="stop", **options):
        super().__init__(api, handle, **options)
        self.source = source
        self.prefill_blocks = len(self.buffers) if prefill_blocks is None else prefill_blocks
        if not isinstance(self.prefill_blocks, int) or not 1 <= self.prefill_blocks <= len(self.buffers):
            raise ValueError("prefill_blocks 必须为 1..queue_depth")
        if not isfinite(prefill_timeout) or prefill_timeout <= 0:
            raise ValueError("prefill_timeout 必须为有限正数")
        if underrun_policy not in ("stop", "zeros"):
            raise ValueError("underrun_policy 必须为 stop 或 zeros")
        self.prefill_timeout, self.underrun_policy = prefill_timeout, underrun_policy
        self.prefilled = threading.Event()
        self.stats = StreamStats("tx")
        self._current = None
        self._position = 0
        self._ended = False
        self._eof_requested = False
        # 强引用一直保留到 native stop 成功，避免 ctypes 回调被 GC 回收。
        self.callback = SampleCallback(self._callback)
        self.flush_callback = FlushCallback(self._flush)
        self.complete_callback = CompleteCallback(self._complete)

    def _produce(self):
        try:
            while not self.cancel.is_set():
                index = self._free_buffer()
                if index is None:
                    return
                slot, used, eof = self.buffers[index], 0, False
                while used < self.block_bytes and not self.cancel.is_set():
                    count = self.source.readinto(slot.view[used:])
                    if not isinstance(count, int) or not 0 <= count <= self.block_bytes - used:
                        raise ValueError("source.readinto 必须返回本次读入的有效字节数")
                    if not count:
                        eof = True
                        break
                    used += count
                    self.stats.source_bytes += count
                if self.cancel.is_set():
                    return
                if used:
                    if used % 2:
                        raise ValueError("cs8 数据末尾含不完整 I/Q 对")
                    # USB 最后一块补齐 512 字节，零填充不计入原始载荷统计。
                    padded = (used + 511) // 512 * 512
                    if padded != used:
                        C.memset(slot.address + used, 0, padded - used)
                    self.ready.put_nowait((index, padded))
                else:
                    self.free.put_nowait(index)
                if self.ready.qsize() >= self.prefill_blocks:
                    self.prefilled.set()
                if eof:
                    if not self.stats.source_bytes:
                        raise ValueError("发送 IQ 数据源为空")
                    self.ready.put_nowait(None)
                    self.prefilled.set()
                    return
        except BaseException as error:
            self._fail(error)
            self.prefilled.set()

    def _callback(self, transfer_pointer):
        try:
            transfer = transfer_pointer.contents
            transfer.valid_length = 0
            self.stats.callbacks += 1
            if self.cancel.is_set() or self.error is not None:
                return 1
            capacity = transfer.buffer_length
            if not transfer.buffer or capacity <= 0 or capacity > self.native_size or capacity % 2:
                raise HackRFError("无效的 TX USB 缓冲区")
            destination = C.cast(transfer.buffer, C.c_void_p).value
            copied = 0
            while copied < capacity:
                if self._current is None:
                    if self._ended:
                        break
                    try:
                        item = self.ready.get_nowait()
                    except queue.Empty:
                        if self.underrun_policy == "stop":
                            raise StreamUnderrun("TX 生产队列欠载；请增加预填充/缓冲或降低生产开销")
                        missing = capacity - copied
                        C.memset(destination + copied, 0, missing)
                        copied += missing
                        self.stats.zero_fill_bytes += missing
                        break
                    if item is None:
                        self._ended = True
                        break
                    self._current, self._position = item, 0
                index, size = self._current
                count = min(capacity - copied, size - self._position)
                C.memmove(destination + copied, self.buffers[index].address + self._position, count)
                copied += count
                self._position += count
                if self._position == size:
                    self.free.put_nowait(index)
                    self._current = None
            transfer.valid_length = copied
            self.stats.submitted_bytes += copied
            if copied:
                # 最后一块有数据时仍返回 0；返回非零会使 libhackrf 丢弃该块。
                return 0
            self._eof_requested = True
            return 1
        except BaseException as error:
            if transfer_pointer:
                transfer_pointer.contents.valid_length = 0
            self._fail(error)
            return 1

    def _complete(self, transfer_pointer, success):
        try:
            if success:
                self.stats.completed_bytes += transfer_pointer.contents.valid_length
            else:
                self._fail(HackRFError("TX USB 传输失败"))
        except BaseException as error:
            self._fail(error)

    def _flush(self, context, success):
        if not success or not self._eof_requested:
            self._fail(HackRFError("TX flush 失败或在源结束前被触发"))
        else:
            self.stats.flush_complete = True
            self.finished.set()

    def run(self):
        worker = threading.Thread(target=self._produce, name="hackrf-tx-source", daemon=True)
        started, registered = False, False
        worker.start()
        begin = time.perf_counter()
        try:
            deadline = time.monotonic() + self.prefill_timeout
            while not self.prefilled.wait(0.05):
                if self.cancel_event is not None and self.cancel_event.is_set():
                    raise StreamCancelled("用户在预填充时取消")
                if time.monotonic() >= deadline:
                    raise TimeoutError("TX 数据源预填充超时")
            if self.error is not None:
                raise self.error
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise StreamCancelled("用户在发送前取消")
            registered, self.stopped = True, False
            self.api.check(self.api.hackrf_enable_tx_flush(self.handle, self.flush_callback, None), "hackrf_enable_tx_flush")
            self.api.check(self.api.hackrf_set_tx_block_complete_callback(self.handle, self.complete_callback), "hackrf_set_tx_block_complete_callback")
            started, self.stopped = True, False
            self.api.check(self.api.hackrf_start_tx(self.handle, self.callback, None), "hackrf_start_tx")
            self._wait()
            if self.stats.completed_bytes != self.stats.submitted_bytes:
                raise HackRFError("TX flush 后 USB 完成字节数与提交字节数不一致")
            return self.stats
        finally:
            self.cancel.set()
            try:
                if registered:
                    self._finish_native(started)
            finally:
                worker.join(timeout=1.0)
                if worker.is_alive() and sys.exception() is not None:
                    sys.exception().add_note("数据源线程仍在 readinto 中；自定义数据源应自行限制阻塞时间")
                self.stats.elapsed_seconds = time.perf_counter() - begin

    def _finish_native(self, started):
        primary = sys.exception()
        try:
            if started:
                self.api.check(self.api.hackrf_stop_tx(self.handle), "hackrf_stop_tx")
            # stop_tx 不清空这些函数指针；若保留，后续 RX 可能调用已 GC 的 TX 回调。
            self.api.check(self.api.hackrf_set_tx_block_complete_callback(self.handle, CompleteCallback()),
                           "hackrf_set_tx_block_complete_callback(NULL)")
            self.api.check(self.api.hackrf_disable_tx_flush(self.handle), "hackrf_disable_tx_flush")
            self.stopped = True
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note(f"停止或清理 TX 回调时另有错误：{error}")


class RxStream(_Stream):
    def __init__(self, api, handle, sink, *, sample_count=None, **options):
        super().__init__(api, handle, **options)
        if sample_count is not None and (not isinstance(sample_count, int) or sample_count < 1):
            raise ValueError("sample_count 必须为正整数或 None（持续接收）")
        if self.block_bytes < self.native_size:
            raise ValueError("RX block_bytes 至少要容纳一个完整 native USB 缓冲")
        self.sink = sink
        self.target_bytes = None if sample_count is None else sample_count * 2
        self.stats = StreamStats("rx")
        self.producer_done = threading.Event()
        self.callback = SampleCallback(self._callback)

    def _callback(self, transfer_pointer):
        try:
            self.stats.callbacks += 1
            if self.cancel.is_set():
                return 1
            transfer = transfer_pointer.contents
            count = transfer.valid_length
            if not transfer.buffer or count < 0 or count > min(transfer.buffer_length, self.block_bytes) or count % 2:
                raise HackRFError("无效的 RX USB 数据长度")
            if self.target_bytes is not None:
                count = min(count, self.target_bytes - self.stats.source_bytes)
            if count:
                try:
                    index = self.free.get_nowait()
                except queue.Empty as error:
                    raise StreamOverrun("RX 缓冲池溢出；消费者未及时处理数据") from error
                C.memmove(self.buffers[index].address, transfer.buffer, count)
                self.ready.put_nowait((index, count))
                self.stats.source_bytes += count
            if self.target_bytes is not None and self.stats.source_bytes >= self.target_bytes:
                self.finished.set()
                return 1
            return 0
        except BaseException as error:
            self._fail(error)
            return 1

    def _consume(self):
        try:
            while True:
                try:
                    index, count = self.ready.get(timeout=0.05)
                except queue.Empty:
                    if self.producer_done.is_set():
                        return
                    continue
                try:
                    offset = 0
                    while offset < count:
                        written = self.sink.write(self.buffers[index].view[offset:count])
                        if not isinstance(written, int) or not 0 < written <= count - offset:
                            raise OSError("sink.write 未完整写入数据")
                        offset += written
                        self.stats.written_bytes += written
                finally:
                    self.free.put_nowait(index)
        except BaseException as error:
            self._fail(error)

    def run(self):
        worker = threading.Thread(target=self._consume, name="hackrf-rx-sink", daemon=True)
        started = False
        begin = time.perf_counter()
        worker.start()
        try:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise StreamCancelled("用户在接收前取消")
            started, self.stopped = True, False
            self.api.check(self.api.hackrf_start_rx(self.handle, self.callback, None), "hackrf_start_rx")
            self._wait()
        finally:
            self.cancel.set()
            try:
                if started:
                    self._stop("hackrf_stop_rx")
            finally:
                self.producer_done.set()
                worker.join(timeout=5.0)
                self.stats.elapsed_seconds = time.perf_counter() - begin
        if worker.is_alive():
            raise TimeoutError("RX 消费者未及时退出")
        if self.error is not None:
            raise self.error
        if self.target_bytes is not None and self.stats.written_bytes != self.target_bytes:
            raise HackRFError("RX 接收字节数与落盘字节数不一致")
        return self.stats
