"""模拟 libhackrf 回调时序，不连接 USB，也不产生射频信号。"""
import ctypes as C
import threading
import time

from flrc_modem.sdr.hackrf.abi import Transfer, DeviceList
from flrc_modem.sdr.hackrf.library import HackRFError


def transfer(data=b"", capacity=512):
    storage = (C.c_uint8 * capacity)()
    if data:
        C.memmove(storage, data, len(data))
    return storage, Transfer(None, storage, capacity, len(data), None, None)


class FakeAPI:
    def __init__(self, *, rx_data=b"", fail_start=False, fail_flush=False, fail_complete=False,
                 buffer_size=512, rx_interval=0.0005, tx_interval=0.0005, tx_start_burst=1,
                 serials=None, open_codes=None):
        self.rx_data = rx_data
        self.buffer_size, self.rx_interval = buffer_size, rx_interval
        self.tx_interval = tx_interval
        self.tx_start_burst = tx_start_burst
        self.fail_start, self.fail_flush, self.fail_complete = fail_start, fail_flush, fail_complete
        self.calls, self.sent = [], bytearray()
        self.stop_event = threading.Event()
        self.worker = None
        self.serials = ("0000000000000000000000000000abcd",) if serials is None else tuple(serials)
        self.open_codes = open_codes or {}
        self._device_lists = {}

    def hackrf_device_list(self):
        self.calls.append("device_list")
        serials = (C.c_char_p * len(self.serials))(*(item.encode() if item is not None else None for item in self.serials))
        listing = DeviceList(serial_numbers=serials, devicecount=len(self.serials))
        self._device_lists[C.addressof(listing)] = (listing, serials)
        return C.pointer(listing)

    def hackrf_device_list_free(self, listing):
        self.calls.append("free_list")
        del self._device_lists[C.addressof(listing.contents)]

    def hackrf_device_list_open(self, listing, index, output):
        self.calls.append(("list_open", index))
        code = self.open_codes.get(index, 0)
        return code if code else self.hackrf_open(output)

    def check(self, code, operation):
        if code:
            raise HackRFError(f"{operation}: fake error {code}")

    def hackrf_get_transfer_buffer_size(self, handle):
        return self.buffer_size

    def hackrf_get_transfer_queue_depth(self, handle):
        return 4

    def acquire(self):
        self.calls.append("acquire")

    def release(self):
        self.calls.append("release")

    def hackrf_open(self, output):
        self.calls.append("open")
        C.cast(output, C.POINTER(C.c_void_p))[0] = C.c_void_p(42)
        return 0

    def hackrf_open_by_serial(self, serial, output):
        self.calls.append(("serial", serial))
        return self.hackrf_open(output)

    def hackrf_close(self, handle):
        self.calls.append("close")
        return 0

    def __getattr__(self, name):
        if name.startswith("hackrf_set_"):
            def setter(handle, value):
                self.calls.append((name, value))
                return 0
            return setter
        raise AttributeError(name)

    def hackrf_enable_tx_flush(self, handle, callback, context):
        self.flush = callback
        return 0

    def hackrf_set_tx_block_complete_callback(self, handle, callback):
        self.complete = callback
        return 0

    def hackrf_disable_tx_flush(self, handle):
        self.calls.append("disable_flush")
        self.flush = None
        return 0

    def hackrf_start_tx(self, handle, callback, context):
        self.calls.append("start_tx")
        if self.fail_start:
            return -1
        self.stop_event.clear()
        def run():
            blocks = 0
            while not self.stop_event.is_set():
                storage, block = transfer(capacity=self.buffer_size)
                result = callback(C.byref(block))
                # 官方库只接受 result==0 且 valid_length>0 的本次数据。
                if result or not block.valid_length:
                    self.calls.append("eof")
                    time.sleep(0.005)
                    self.calls.append("flush")
                    self.flush(None, int(not self.fail_flush))
                    return
                self.sent.extend(C.string_at(block.buffer, block.valid_length))
                self.complete(C.byref(block), int(not self.fail_complete))
                blocks += 1
                if blocks >= self.tx_start_burst:
                    time.sleep(self.tx_interval)
        self.worker = threading.Thread(target=run, name="fake-usb", daemon=True)
        self.worker.start()
        return 0

    def hackrf_stop_tx(self, handle):
        self.calls.append("stop_tx")
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(1)
        return 0

    def hackrf_start_rx(self, handle, callback, context):
        self.calls.append("start_rx")
        if self.fail_start:
            return -1
        self.stop_event.clear()
        def run():
            for offset in range(0, len(self.rx_data), self.buffer_size):
                if self.stop_event.is_set():
                    break
                storage, block = transfer(self.rx_data[offset:offset + self.buffer_size], self.buffer_size)
                if callback(C.byref(block)):
                    break
                time.sleep(self.rx_interval)
        self.worker = threading.Thread(target=run, name="fake-usb", daemon=True)
        self.worker.start()
        return 0

    def hackrf_stop_rx(self, handle):
        self.calls.append("stop_rx")
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(1)
        return 0
