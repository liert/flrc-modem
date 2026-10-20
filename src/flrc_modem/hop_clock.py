"""DK32S 稀疏时间戳跟踪；漏解帧不停止时钟，未知 IQ 缺口必须显式失效。"""
from collections import deque
from dataclasses import asdict, dataclass
from math import floor, isfinite
from statistics import median


TICK_RATE = 10000
TICK_MODULUS = 1 << 32


@dataclass(frozen=True)
class HopClockPrediction:
    sample_time_s: float
    remote_time_s: float
    timestamp_ticks: int
    slot_index: int
    slot_candidates: tuple[int, ...]
    uncertainty_s: float
    age_s: float
    state: str
    anchor_count: int

    def to_dict(self):
        return asdict(self)


class HopClockTracker:
    """在同一连续采样段内估计 remote_time = offset + rate * sample_time。

    sample_time_s 必须来自采样位置，不能传入解码完成时的墙钟时间。
    uncertainty 是在配置的相对时钟误差界成立时的工程估计，不是硬件定时保证。
    """
    def __init__(self, *, sync_word=None, window=64, min_fit_span=0.5,
                 max_rate_error_ppm=200, outlier_gate_s=0.002,
                 max_uncertainty_s=0.005, holdover_after_s=0.1):
        if not isinstance(window, int) or not 3 <= window <= 256:
            raise ValueError("window 必须为3..256")
        for name, value in (("min_fit_span", min_fit_span), ("max_rate_error_ppm", max_rate_error_ppm),
                            ("outlier_gate_s", outlier_gate_s), ("max_uncertainty_s", max_uncertainty_s),
                            ("holdover_after_s", holdover_after_s)):
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} 必须为有限正数")
        if max_rate_error_ppm >= 1000000:
            raise ValueError("相对时钟误差界必须小于1000000ppm")
        self.sync_word = bytes.fromhex(sync_word).hex() if sync_word is not None else None
        if self.sync_word is not None and len(self.sync_word) != 8:
            raise ValueError("同步字必须为4B")
        self.points = deque(maxlen=window)
        self.min_fit_span, self.rate_bound = min_fit_span, max_rate_error_ppm * 1e-6
        self.outlier_gate_s, self.max_uncertainty_s = outlier_gate_s, max_uncertainty_s
        self.holdover_after_s = holdover_after_s
        self.accepted = self.rejected = self.filtered = self.duplicates = 0
        self.generation = 0
        self.reason = None
        self.rate, self.ref_sample, self.ref_remote = 1.0, 0.0, 0.0
        self.residual_s = 0.0

    def invalidate(self, reason="采样连续性中断"):
        """硬件丢样且数量未知、换采集文件或重调谐后，不沿用旧采样时间基准。"""
        self.points.clear()
        self.generation += 1
        self.reason = reason
        self.rate, self.ref_sample, self.ref_remote, self.residual_s = 1.0, 0.0, 0.0, 0.0

    @property
    def locked(self):
        return len(self.points) >= 3 and self.points[-1][0] - self.points[0][0] >= self.min_fit_span

    def _estimate(self, sample_time_s):
        return self.ref_remote + self.rate * (sample_time_s - self.ref_sample)

    def _fit(self):
        points = list(self.points)
        ref = points[-1][0]
        # 中位数斜率/截距降低单个坏锚点的影响；短距离点对受100µs量化影响太大，不用于拟合频差。
        slopes = [(b[1] - a[1]) / (b[0] - a[0])
                  for i, a in enumerate(points) for b in points[i + 1:]
                  if b[0] - a[0] >= self.min_fit_span]
        rate = median(slopes) if slopes else self.rate
        if abs(rate - 1) > self.rate_bound:
            rate = self.rate
        intercept = median(y - rate * (x - ref) for x, y in points)
        inliers = [(x, y) for x, y in points
                   if abs(y - (intercept + rate * (x - ref))) <= self.outlier_gate_s]
        if not inliers:
            return
        self.rejected += len(points) - len(inliers)
        self.points.clear()
        self.points.extend(inliers)
        self.ref_sample = inliers[-1][0]
        self.ref_remote = intercept + rate * (self.ref_sample - ref)
        self.rate = rate
        self.residual_s = max(abs(y - self._estimate(x)) for x, y in inliers)

    def observe(self, sample_time_s, timestamp_ticks):
        if (not isfinite(sample_time_s) or sample_time_s < 0
                or isinstance(timestamp_ticks, bool) or not isinstance(timestamp_ticks, int)
                or not 0 <= timestamp_ticks < TICK_MODULUS):
            raise ValueError("锚点必须包含非负采样时间和32bit无符号时间戳")
        if self.points and sample_time_s <= self.points[-1][0]:
            if sample_time_s == self.points[-1][0] and timestamp_ticks == round(self.points[-1][1] * TICK_RATE) % TICK_MODULUS:
                self.duplicates += 1
            else:
                self.rejected += 1
            return False
        remote = timestamp_ticks / TICK_RATE
        if self.points:
            expected = self._estimate(sample_time_s)
            wrap_s = TICK_MODULUS / TICK_RATE
            remote += round((expected - remote) / wrap_s) * wrap_s
            age = sample_time_s - self.points[-1][0]
            if self.locked and abs(remote - expected) > self.outlier_gate_s + age * self.rate_bound:
                self.rejected += 1
                return False
        self.points.append((float(sample_time_s), remote))
        self._fit()
        retained = any(x == sample_time_s for x, _ in self.points)
        if retained:
            self.accepted += 1
            self.reason = None
        return retained

    def observe_record(self, record):
        """只采纳CRC/sum8成功的地面遥控时间戳；排除自身发送包和其他链路。"""
        application = record.get("application", {})
        if (record.get("crc_ok") is not True or application.get("sum8_ok") is not True
                or application.get("direction") != "ground_to_air"
                or application.get("command") not in (0x11, 0x16)
                or record.get("exchange_class") == "tx_echo"
                or (self.sync_word is not None and record.get("sync_word_hex", "").lower() != self.sync_word)):
            self.filtered += 1
            return False
        if self.sync_word is None:
            sync = bytes.fromhex(record["sync_word_hex"])
            if len(sync) != 4:
                raise ValueError("同步字必须为4B")
            self.sync_word = sync.hex()
        return self.observe(record["time_s"], application["timestamp_ticks"])

    def predict(self, sample_time_s):
        if not self.points:
            return None
        if not isfinite(sample_time_s) or sample_time_s < self.points[-1][0]:
            raise ValueError("预测时间不能早于最后一个有效锚点")
        remote = self._estimate(sample_time_s)
        age = sample_time_s - self.points[-1][0]
        uncertainty = 0.5 / TICK_RATE + self.residual_s + age * self.rate_bound
        ticks = floor(remote * TICK_RATE) % TICK_MODULUS
        lo = floor((remote - uncertainty) * TICK_RATE)
        hi = floor((remote + uncertainty) * TICK_RATE)
        candidates = (tuple(range(20)) if hi - lo >= TICK_RATE else
                      tuple(sorted({((tick % TICK_MODULUS) % TICK_RATE) // 500 for tick in range(lo, hi + 1)})))
        state = ("acquiring" if not self.locked else "uncertain" if uncertainty > self.max_uncertainty_s
                 else "holdover" if age > self.holdover_after_s else "tracking")
        return HopClockPrediction(sample_time_s, remote, ticks, (ticks % TICK_RATE) // 500,
                                  candidates, uncertainty, age, state, len(self.points))

    def snapshot(self):
        return {"anchor_count": len(self.points), "accepted_updates": self.accepted,
                "rejected_updates": self.rejected, "filtered_records": self.filtered,
                "duplicate_records": self.duplicates, "clock_rate": self.rate,
                "rate_error_ppm": (self.rate - 1) * 1e6, "residual_s": self.residual_s,
                "assumed_relative_rate_bound_ppm": self.rate_bound * 1e6,
                "locked": self.locked, "generation": self.generation, "reason": self.reason}
