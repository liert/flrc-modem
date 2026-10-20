"""双 HackRF 有限发送/连续采集；先确认收到采样，再发送，结束后留出回传时间。"""
from contextlib import ExitStack
from dataclasses import asdict
from math import isfinite
from pathlib import Path
import threading
import time

from .sdr.hackrf import HackRFDevice, HackRFLibrary, StreamCancelled, StreamOverrun


class _CaptureSink:
    def __init__(self, stream, ready_bytes):
        self.stream, self.ready_bytes = stream, ready_bytes
        self.written_bytes = 0
        self.ready = threading.Event()

    def write(self, data):
        count = self.stream.write(data)
        self.written_bytes += count
        if self.written_bytes >= self.ready_bytes:
            self.ready.set()
        return count


class ExchangeCapture:
    """source 为有限 cs8 readinto 源。只采集，停止射频后由调用者解调，避免 DSP 抢占采集。"""
    def __init__(self, tx_config, rx_config, *, tx_serial, rx_serial,
                 pre_roll=0.2, post_roll=0.3, timeout=30, dll_path=None,
                 device_factory=None):
        if not tx_serial or not rx_serial:
            raise ValueError("双设备收发必须明确指定 tx_serial 和 rx_serial")
        for name, value in (("pre_roll", pre_roll), ("post_roll", post_roll), ("timeout", timeout)):
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须为有限正数")
        self.tx_config, self.rx_config = tx_config, rx_config
        self.tx_serial, self.rx_serial = tx_serial, rx_serial
        self.pre_roll, self.post_roll, self.timeout = pre_roll, post_roll, timeout
        self.dll_path, self.device_factory = dll_path, device_factory
        self.stats = {"status": "created", "tx_device": None, "rx_device": None,
                      "tx": None, "rx": None, "error": None,
                      "tx_config": asdict(tx_config), "rx_config": asdict(rx_config),
                      "pre_roll_seconds": pre_roll, "post_roll_seconds": post_roll,
                      "rx_ready_before_tx": False, "elapsed_seconds": 0}

    def run(self, source, iq_path, *, transmit_operation=None):
        """可传入 transmit_operation(device, cancel_event) 在持续接收期间执行有限跳频计划。"""
        if self.stats["status"] != "created":
            raise RuntimeError("采集会话不能重复运行")
        begin = time.monotonic()
        self.stats["status"] = "running"
        cancel, done = threading.Event(), threading.Event()
        errors = []
        worker = tx = rx = None
        try:
            # x 模式防止覆盖用户的原始 IQ；两个设备打开成功后才启动 RF。
            with Path(iq_path).open("xb", buffering=0) as stream, ExitStack() as stack:
                api = None if self.device_factory else HackRFLibrary(self.dll_path)
                def open_device(serial):
                    return (self.device_factory(serial) if self.device_factory else
                            HackRFDevice(api, serial=serial))
                tx = stack.enter_context(open_device(self.tx_serial))
                self.stats["tx_device"] = tx.selected_device
                # 在打开第二次之前解析真实身份，防止不同后缀指向同一设备。
                actual_tx = tx.selected_device["serial"]
                if actual_tx and actual_tx.lower().endswith(self.rx_serial.lower()):
                    raise ValueError("TX 和 RX 选择了同一台 HackRF")
                rx = stack.enter_context(open_device(self.rx_serial))
                self.stats["rx_device"] = rx.selected_device
                if tx.selected_device["serial"] == rx.selected_device["serial"]:
                    raise ValueError("TX 和 RX 必须是两台不同的 HackRF")
                sink = _CaptureSink(stream, max(2, round(self.pre_roll * self.rx_config.sample_rate) * 2))

                def capture():
                    try:
                        rx.receive(sink, self.rx_config, cancel_event=cancel,
                                   timeout=self.timeout + self.pre_roll + self.post_roll + 10,
                                   queue_depth=32)
                        if not cancel.is_set():
                            raise RuntimeError("接收在发送完成前意外结束")
                    except StreamCancelled as error:
                        if not cancel.is_set():
                            errors.append(error)
                    except BaseException as error:
                        errors.append(error)
                        cancel.set()
                    finally:
                        done.set()

                worker = threading.Thread(target=capture, name="flrc-exchange-rx", daemon=True)
                worker.start()
                try:
                    deadline = time.monotonic() + self.pre_roll + 5
                    while not sink.ready.wait(0.02):
                        if done.is_set():
                            raise errors[0] if errors else RuntimeError("接收尚未就绪就停止")
                        if time.monotonic() >= deadline:
                            raise TimeoutError("接收端未积累到预设采样数，取消发送")
                    if done.is_set():
                        raise errors[0] if errors else RuntimeError("接收已经停止，取消发送")
                    self.stats["rx_ready_before_tx"] = True
                    if transmit_operation is None:
                        tx.transmit(source, self.tx_config, timeout=self.timeout, cancel_event=cancel,
                                    queue_depth=8, prefill_blocks=4)
                    else:
                        self.stats["transmission"] = transmit_operation(tx, cancel)
                    # 用实际采样数而不是主机 sleep 判断尾部采集，USB 排队/调度延迟不能缩短接收窗口。
                    tail_start = sink.written_bytes
                    tail_target = tail_start + max(2, round(self.post_roll * self.rx_config.sample_rate) * 2)
                    tail_wall_end = time.monotonic() + self.post_roll
                    tail_deadline = tail_wall_end + 5
                    # 消费者排空 TX 期间积压的旧块不能冒充 TX 结束后的新采样时间。
                    while sink.written_bytes < tail_target or time.monotonic() < tail_wall_end:
                        if done.wait(0.01):
                            raise errors[0] if errors else RuntimeError("回传采集提前结束")
                        if time.monotonic() >= tail_deadline:
                            raise TimeoutError("回传采集未积累到预设尾部采样数")
                    self.stats["post_roll_samples"] = (sink.written_bytes - tail_start) // 2
                finally:
                    cancel.set()
                    worker.join(timeout=15)
                    if worker.is_alive():
                        raise TimeoutError("接收线程未能及时停止")
                if errors:
                    raise errors[0]
                # receive 通过主动取消结束时，底层不会走正常返回后的健康检查。
                # 在这里核对已保存的停止后固件状态，不能把丢样采集报告为成功。
                capture_stats = rx.last_stats
                if (capture_stats is None or capture_stats.source_bytes != capture_stats.written_bytes
                        or capture_stats.written_bytes != sink.written_bytes):
                    raise StreamOverrun("接收字节数与原始 IQ 写入字节数不一致")
                state = capture_stats.device_state
                if state is not None and (state["error"] or state["num_shortfalls"]):
                    raise StreamOverrun(f"接收停止后的固件状态异常：{state}")
                self.stats["rx_continuity_verified"] = state is not None
                self.stats["status"] = "completed"
        except BaseException as error:
            self.stats["status"] = "cancelled" if isinstance(error, KeyboardInterrupt) else "failed"
            self.stats["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            for name, device in (("tx", tx), ("rx", rx)):
                if device is not None and device.last_stats is not None:
                    self.stats[name] = device.last_stats.to_dict()
            self.stats["elapsed_seconds"] = time.monotonic() - begin
        return self.stats


def classify_record(record, sent_packets, reply_syncs=()):
    """不把收到了自己发送的帧当成回复，也不把仅有 ACK 标志的帧当成因果证明。"""
    key = (record["sync_word_hex"], record["payload_hex"])
    if key in sent_packets:
        return "tx_echo"
    application = record.get("application", {})
    if (record["sync_word_hex"] in ({sync for sync, _ in sent_packets} | set(reply_syncs))
            and application.get("direction") == "air_to_ground"):
        return "air_to_ground_candidate"
    return "other"
