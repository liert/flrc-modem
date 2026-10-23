"""Isolate native USB callbacks from the API and its CPU-intensive DSP threads."""
from dataclasses import asdict
import logging
import multiprocessing
from multiprocessing import shared_memory
import queue
import time

from ..diagnostics import enable_fault_log, fault_log_path

logger = logging.getLogger(__name__)
SLOT_BYTES = 1024 * 1024
SLOT_COUNT = 8


class _ChunkSink:
    def __init__(self, chunks, free, memory, sample_rate):
        self.chunks, self.free, self.memory, self.sample_rate = chunks, free, memory, sample_rate
        self.dropped = 0
        self.gap = False

    def write(self, buffer):
        timestamp = time.time() - len(buffer) / (2 * self.sample_rate)
        for offset in range(0, len(buffer), SLOT_BYTES):
            try:
                index = self.free.get_nowait()
            except queue.Empty:
                # Never block USB consumption on the API/DSP consumer.
                self.dropped += 1
                self.gap = True
                continue
            count = min(SLOT_BYTES, len(buffer) - offset)
            start = index * SLOT_BYTES
            self.memory.buf[start:start + count] = buffer[offset:offset + count]
            self.chunks.put_nowait((index, count, timestamp + offset / (2 * self.sample_rate),
                                    self.gap, self.dropped))
            self.gap = False
        return len(buffer)


def _receive_worker(config_values, serial, device_index, chunks, free, memory_name, events, cancel):
    enable_fault_log('spectrum-hackrf')
    # Never wait for an undrained IQ queue during shutdown after cancellation.
    chunks.cancel_join_thread()
    free.cancel_join_thread()
    memory = None
    try:
        from flrc_modem.sdr.hackrf.config import HackRFConfig
        from flrc_modem.sdr.hackrf.device import HackRFDevice
        from flrc_modem.sdr.hackrf.streaming import StreamCancelled

        config = HackRFConfig(**config_values)
        memory = shared_memory.SharedMemory(name=memory_name)
        with HackRFDevice(serial=serial, device_index=device_index) as device:
            events.send(('opened', device.selected_device))
            sink = _ChunkSink(chunks, free, memory, config.sample_rate)
            try:
                device.receive(sink, config, sample_count=None, cancel_event=cancel,
                               timeout=None, queue_depth=32)
            except StreamCancelled:
                if not cancel.is_set():
                    raise
        events.send(('stopped', None))
    except Exception as error:
        events.send(('error', f'{type(error).__name__}: {error}'))
    finally:
        if memory is not None:
            memory.close()
        events.close()


class HackRFSpectrumReceiver:
    """Parent-side receiver; only the spawned worker loads/opens libhackrf."""

    def __init__(self, *, serial=None, device_index=None):
        self.serial, self.device_index = serial, device_index
        self._process = self._cancel = self._chunks = self._events = None
        self._free = self._memory = None

    def receive(self, on_chunk, config, *, cancel_event, on_open):
        context = multiprocessing.get_context('spawn')
        self._chunks = context.Queue(maxsize=SLOT_COUNT)
        self._free = context.Queue(maxsize=SLOT_COUNT)
        self._memory = shared_memory.SharedMemory(create=True, size=SLOT_COUNT * SLOT_BYTES)
        for index in range(SLOT_COUNT):
            self._free.put_nowait(index)
        self._events, child_events = context.Pipe(duplex=False)
        self._cancel = context.Event()
        process = context.Process(target=_receive_worker,
            args=(asdict(config), self.serial, self.device_index, self._chunks, self._free,
                  self._memory.name, child_events, self._cancel),
            name='spectrum-hackrf-rx', daemon=True)
        self._process = process
        try:
            process.start()
            child_events.close()
            logger.info('HackRF 接收子进程 pid=%s，sample_rate=%s，frequency=%s',
                        process.pid, config.sample_rate, config.frequency_hz)
            opened = False
            last_data = time.monotonic()
            while not cancel_event.is_set():
                while self._events.poll():
                    try:
                        kind, value = self._events.recv()
                    except EOFError:
                        break
                    if kind == 'opened':
                        opened = True
                        last_data = time.monotonic()
                        on_open(value or {})
                    elif kind == 'error':
                        raise RuntimeError(value)
                    elif kind == 'stopped':
                        raise RuntimeError('HackRF 接收子进程意外结束')
                if not process.is_alive():
                    process.join(timeout=0)
                    code = process.exitcode
                    fault_path = fault_log_path('spectrum-hackrf', process.pid)
                    detail = '原生内存访问冲突' if code is not None and code & 0xFFFFFFFF == 0xC0000005 else '异常退出'
                    status = f'0x{code & 0xFFFFFFFF:08X}' if code is not None else '未知'
                    raise RuntimeError(f'HackRF 驱动子进程{detail}（退出码 {status}）；堆栈日志：{fault_path}')
                try:
                    index, count, timestamp, gap, dropped = self._chunks.get(timeout=.05)
                except queue.Empty:
                    if opened and time.monotonic() - last_data > 5:
                        raise TimeoutError('HackRF 超过 5 秒未收到 IQ 数据，请检查设备连接')
                    continue
                last_data = time.monotonic()
                # Only small slot descriptors travel over the pipe. A native
                # crash cannot leave the parent reading half of a large IQ message.
                start = index * SLOT_BYTES
                data = bytes(self._memory.buf[start:start + count])
                self._free.put_nowait(index)
                on_chunk(data, timestamp, gap, dropped)
        finally:
            child_events.close()
            self.close()

    def close(self):
        process = self._process
        if process is not None and process.pid is not None:
            self._cancel.set()
            process.join(timeout=1.5)
            if process.is_alive():
                logger.warning('HackRF 子进程 pid=%s 未及时停止，终止该接收子进程', process.pid)
                process.terminate()
                process.join(timeout=1)
            if process.is_alive():
                raise RuntimeError('HackRF 接收子进程仍未退出，保留设备占用')
            logger.info('HackRF 接收子进程 pid=%s 已退出，exitcode=%s', process.pid, process.exitcode)
            process.close()
        self._process = None
        if self._events is not None:
            self._events.close()
            self._events = None
        if self._chunks is not None:
            self._chunks.close()
            self._chunks = None
        if self._free is not None:
            self._free.close()
            self._free = None
        if self._memory is not None:
            self._memory.close()
            self._memory.unlink()
            self._memory = None
