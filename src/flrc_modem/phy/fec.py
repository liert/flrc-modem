"""K=7 卷积编码及 Viterbi：CR=1/2、3/4，以及 CR=1 不编码。"""
import numpy as np

from .profile import validate_coding_rate

GENERATORS = (0x6D, 0x4F)
PUNCTURE = ((0, 1), (0,), (1,))
PATTERNS = {"1/2": ((0, 1),), "3/4": PUNCTURE}
STATES = np.arange(64)
PREVIOUS = np.stack((STATES >> 1, (STATES >> 1) | 32))
REGISTERS = (PREVIOUS << 1) | (STATES & 1)
EXPECTED = np.stack([
    np.array([[(int(reg) & generator).bit_count() & 1 for reg in row] for row in REGISTERS])
    for generator in GENERATORS
])


def coded_length(steps: int, coding_rate: str = "3/4") -> int:
    """编码区长度，不包含前导与同步字；允许不完整穿孔周期。"""
    validate_coding_rate(coding_rate)
    if steps < 0:
        raise ValueError("输入比特数不能为负数")
    if coding_rate == "1":
        return steps
    if coding_rate == "1/2":
        return steps * 2
    cycles, remainder = divmod(steps, 3)
    return cycles * 4 + (0, 2, 3)[remainder]


def convolutional_encode(input_bits, coding_rate: str = "3/4") -> np.ndarray:
    """零初态编码及所选穿孔模式；CR=1 直接透传，不添加尾比特。"""
    validate_coding_rate(coding_rate)
    bits = np.asarray(input_bits)
    if bits.ndim != 1 or not np.all((bits == 0) | (bits == 1)):
        raise ValueError("卷积编码输入必须是一维 0/1 比特")
    if coding_rate == "1":
        return bits.astype(np.uint8, copy=True)
    pattern = PATTERNS[coding_rate]
    output = np.empty(coded_length(len(bits), coding_rate), dtype=np.uint8)
    state, cursor = 0, 0
    for index, bit in enumerate(bits):
        state = ((state << 1) | int(bit)) & 0x7F
        for selection in pattern[index % len(pattern)]:
            output[cursor] = (state & GENERATORS[selection]).bit_count() & 1
            cursor += 1
    return output


def _decode_arguments(coded_bits, steps: int | None, coding_rate: str):
    validate_coding_rate(coding_rate)
    bits = np.asarray(coded_bits, dtype=np.uint8)
    if steps is None:
        steps = {"1/2": len(bits) // 2, "3/4": len(bits) * 3 // 4, "1": len(bits)}[coding_rate]
    if steps < 0 or coded_length(steps, coding_rate) > len(bits):
        raise ValueError("编码比特不足")
    return bits, steps


# 穿孔模式展开成扁平表，供 JIT 内核按步取用生成多项式。
_PATTERN_TABLES = {
    rate: (np.array([g for selection in pattern for g in selection], dtype=np.int64),
           np.cumsum([0] + [len(selection) for selection in pattern[:-1]]).astype(np.int64),
           np.array([len(selection) for selection in pattern], dtype=np.int64))
    for rate, pattern in PATTERNS.items()
}
_EXPECTED_U8 = np.ascontiguousarray(EXPECTED, dtype=np.uint8)
_PREVIOUS_I64 = np.ascontiguousarray(PREVIOUS, dtype=np.int64)


def _viterbi_kernel(bits, steps, flat, offsets, counts, expected, previous, terminated):
    """与 _convolutional_decode_reference 逐步等价（含平局取前驱 0、argmin 取首个）。"""
    inf = np.inf
    metrics = np.full(64, inf)
    metrics[0] = 0.0
    updated = np.empty(64)
    choices = np.empty((steps, 64), dtype=np.uint8)
    period = counts.shape[0]
    cursor = 0
    for step in range(steps):
        phase = step % period
        start, count = offsets[phase], counts[phase]
        for state in range(64):
            best = inf
            choice = 0
            for option in range(2):
                metric = metrics[previous[option, state]]
                for index in range(count):
                    if expected[flat[start + index], option, state] != bits[cursor + index]:
                        metric += 1.0
                if option == 0 or metric < best:
                    best = metric
                    choice = option
            updated[state] = best
            choices[step, state] = choice
        cursor += count
        for state in range(64):
            metrics[state] = updated[state]
    state = 0
    if not terminated:
        for candidate in range(1, 64):
            if metrics[candidate] < metrics[state]:
                state = candidate
    metric = metrics[state]
    decoded = np.zeros(steps, dtype=np.uint8)
    for step in range(steps - 1, -1, -1):
        decoded[step] = state & 1
        state = previous[choices[step, state], state]
    return decoded, metric


try:
    from numba import njit
except ImportError:  # 无 numba 时退回纯 NumPy 参考实现，功能相同但较慢。
    _jit_kernel = None
else:
    # nogil：解码期间释放 GIL，不阻塞 SDR 接收回调、跳频调度与 WebUI 线程。
    _jit_kernel = njit(cache=True, nogil=True)(_viterbi_kernel)
_warmed = False


def warm_up() -> bool:
    """提前触发 JIT 编译/加载缓存，避免实时线程的首个包承担编译延迟。"""
    global _warmed
    if _jit_kernel is not None and not _warmed:
        convolutional_decode(np.zeros(8, dtype=np.uint8), terminated=True)
        from ..dsp.detector import warm_up_dsp
        warm_up_dsp()
        _warmed = True
    return _jit_kernel is not None


def convolutional_decode(coded_bits, steps: int | None = None, terminated: bool = False,
                         coding_rate: str = "3/4"):
    if _jit_kernel is None:
        return _convolutional_decode_reference(coded_bits, steps, terminated, coding_rate)
    bits, steps = _decode_arguments(coded_bits, steps, coding_rate)
    if coding_rate == "1":
        return bits[:steps].copy(), 0.0
    flat, offsets, counts = _PATTERN_TABLES[coding_rate]
    decoded, metric = _jit_kernel(np.ascontiguousarray(bits), steps, flat, offsets, counts,
                                  _EXPECTED_U8, _PREVIOUS_I64, bool(terminated))
    return decoded, float(metric)


def _convolutional_decode_reference(coded_bits, steps: int | None = None, terminated: bool = False,
                                    coding_rate: str = "3/4"):
    validate_coding_rate(coding_rate)
    bits = np.asarray(coded_bits, dtype=np.uint8)
    if steps is None:
        steps = {"1/2": len(bits) // 2, "3/4": len(bits) * 3 // 4, "1": len(bits)}[coding_rate]
    if steps < 0 or coded_length(steps, coding_rate) > len(bits):
        raise ValueError("编码比特不足")
    if coding_rate == "1":
        return bits[:steps].copy(), 0.0
    pattern = PATTERNS[coding_rate]
    metrics = np.full(64, np.inf)
    metrics[0] = 0
    choices = np.zeros((steps, 64), dtype=np.uint8)
    cursor = 0
    for step in range(steps):
        branch = np.zeros((2, 64))
        for generator in pattern[step % len(pattern)]:
            branch += EXPECTED[generator] != bits[cursor]
            cursor += 1
        candidates = metrics[PREVIOUS] + branch
        choose = candidates[1] < candidates[0]
        choices[step] = choose
        metrics = np.where(choose, candidates[1], candidates[0])
    # 有尾部六个零比特时末态必须回到零；预读硬件头时不限制末态。
    state = 0 if terminated else int(np.argmin(metrics))
    metric = float(metrics[state])
    decoded = np.zeros(steps, dtype=np.uint8)
    for step in range(steps - 1, -1, -1):
        decoded[step] = state & 1
        state = int(PREVIOUS[choices[step, state], state])
    return decoded, metric
