"""单个 DSP 子进程：USB 回调与 NumPy 解调不争用同一个解释器 GIL。"""
from dataclasses import asdict
import multiprocessing as mp
import time
import traceback

from ..io.iq import decode_samples
from ..pipeline import WindowDecoder


class RealtimeDecodeError(RuntimeError):
    pass


def _decode_worker(connection, config):
    try:
        decoder = WindowDecoder(config)
        connection.send(("ready",))
        while True:
            command = connection.recv()
            if command is None:
                return
            start_time_s, raw = command
            begin = time.perf_counter()
            frames = list(decoder.decode(decode_samples(raw, "cs8"), start_time_s))
            connection.send(("frames", frames, asdict(decoder.stats), time.perf_counter() - begin))
    except EOFError:
        pass
    except BaseException:
        try:
            connection.send(("error", traceback.format_exc()))
        except (BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


class ProcessDecoder:
    def __init__(self, config):
        # 显式 spawn，避免在已加载 USB 库的解释器中 fork。
        context = mp.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_decode_worker, args=(child, config),
                                       name="flrc-dsp", daemon=True)
        try:
            self.process.start()
            child.close()
            message = self._receive(timeout=30)
            if message[0] != "ready":
                raise RealtimeDecodeError(str(message))
        except BaseException:
            child.close()
            self.close()
            raise

    def _receive(self, timeout=60):
        deadline = time.monotonic() + timeout
        while not self.connection.poll(0.05):
            if not self.process.is_alive():
                raise RealtimeDecodeError(f"DSP 子进程异常退出：{self.process.exitcode}")
            if time.monotonic() >= deadline:
                raise RealtimeDecodeError("DSP 子进程响应超时")
        try:
            result = self.connection.recv()
        except (EOFError, OSError) as error:
            raise RealtimeDecodeError("DSP 子进程连接中断") from error
        if result[0] == "error":
            raise RealtimeDecodeError(result[1])
        return result

    def decode(self, raw, start_time_s):
        # 每次只交出一个窗口，没有额外的无界进程队列。
        try:
            self.connection.send((start_time_s, raw))
        except (BrokenPipeError, OSError) as error:
            raise RealtimeDecodeError("无法向 DSP 子进程提交窗口") from error
        _, frames, stats, elapsed = self._receive()
        return frames, stats, elapsed

    def close(self):
        if self.process.pid is not None:
            try:
                if self.process.is_alive():
                    self.connection.send(None)
            except (BrokenPipeError, OSError):
                pass
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=2)
            self.process.close()
        self.connection.close()
