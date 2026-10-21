"""SDR 实时频谱与瀑布图单元与集成测试：
验证单音位置、正负频偏、幅度归一化、FFT 点数无关性、平滑平均、会话生命周期与设备互斥控制。
"""
import math
import numpy as np
import pytest

from webui.backend.app.devices.manager import get_device_manager
from webui.backend.app.spectrum.dsp import (
    compute_spectrum,
    convert_raw_iq_to_complex,
    generate_test_tone_iq,
    get_window_and_coherent_gain,
)
from webui.backend.app.spectrum.manager import get_spectrum_manager
from webui.backend.app.spectrum.schemas import SpectrumStartRequest, SpectrumUpdateRequest


def test_window_and_coherent_gain_normalization():
    """验证各窗函数的相干增益计算正确且非零。"""
    for n_fft in (256, 512, 1024, 2048, 4096):
        for w_name in ("hann", "hamming", "blackman", "rectangular"):
            w, s1 = get_window_and_coherent_gain(n_fft, w_name)
            assert len(w) == n_fft
            assert s1 > 0.0
            if w_name == "rectangular":
                assert math.isclose(s1, float(n_fft), rel_tol=1e-5)
            elif w_name == "hann":
                assert math.isclose(s1, float(n_fft) * 0.5, rel_tol=1e-2)


def test_raw_iq_conversion_formats():
    """验证 cs8、sc16、fc32 各种 IQ 格式到 complex64 [-1.0, 1.0] 的正确归一化。"""
    # 1. cs8: int8 极值 [127, -128]
    raw_cs8 = np.array([127, -128, 0, 64], dtype=np.int8).tobytes()
    c_cs8 = convert_raw_iq_to_complex(raw_cs8, "cs8")
    assert len(c_cs8) == 2
    assert math.isclose(c_cs8[0].real, 127.0 / 128.0, rel_tol=1e-3)
    assert math.isclose(c_cs8[0].imag, -128.0 / 128.0, rel_tol=1e-3)

    # 2. sc16: int16 极值 [32767, -32768]
    raw_sc16 = np.array([32767, -32768], dtype="<i2").tobytes()
    c_sc16 = convert_raw_iq_to_complex(raw_sc16, "sc16")
    assert len(c_sc16) == 1
    assert math.isclose(c_sc16[0].real, 32767.0 / 32768.0, rel_tol=1e-3)
    assert math.isclose(c_sc16[0].imag, -32768.0 / 32768.0, rel_tol=1e-3)

    # 3. fc32: float32 [0.5, -0.75]
    raw_fc32 = np.array([0.5, -0.75], dtype="<f4").tobytes()
    c_fc32 = convert_raw_iq_to_complex(raw_fc32, "fc32")
    assert len(c_fc32) == 1
    assert math.isclose(c_fc32[0].real, 0.5, rel_tol=1e-3)
    assert math.isclose(c_fc32[0].imag, -0.75, rel_tol=1e-3)


def test_single_tone_peak_and_positive_negative_offsets():
    """验证正负频偏单音的位置能被双边 FFT 精准定位到正负频率侧。"""
    sample_rate = 10000000.0  # 10 MSps
    center_freq = 2480000000.0  # 2.48 GHz
    n_fft = 2048
    rbw = sample_rate / n_fft  # ~4.88 kHz

    # 1. 正频偏 +2.5 MHz
    pos_offset = 2500000.0
    pos_iq = generate_test_tone_iq(sample_rate, n_fft, pos_offset, amplitude_dbfs=-6.0)
    pos_spec = compute_spectrum(pos_iq, n_fft=n_fft, sample_rate=sample_rate, center_freq=center_freq)
    expected_pos_freq = center_freq + pos_offset
    assert abs(pos_spec["peak_freq"] - expected_pos_freq) <= rbw
    assert math.isclose(pos_spec["peak_power"], -6.0, abs_tol=0.5)

    # 2. 负频偏 -3.2 MHz
    neg_offset = -3200000.0
    neg_iq = generate_test_tone_iq(sample_rate, n_fft, neg_offset, amplitude_dbfs=-10.0)
    neg_spec = compute_spectrum(neg_iq, n_fft=n_fft, sample_rate=sample_rate, center_freq=center_freq)
    expected_neg_freq = center_freq + neg_offset
    assert abs(neg_spec["peak_freq"] - expected_neg_freq) <= rbw
    assert math.isclose(neg_spec["peak_power"], -10.0, abs_tol=0.5)


def test_amplitude_normalization_invariance_across_fft_sizes():
    """验证 0 dBFS 满量程单音在切换 512、1024、2048、4096 点 FFT 时，峰值幅度恒定在 0 dBFS。"""
    sample_rate = 8000000.0
    center_freq = 2480000000.0
    offset_hz = 1000000.0

    peak_powers = []
    for n_fft in (512, 1024, 2048, 4096):
        iq = generate_test_tone_iq(
            sample_rate, n_fft, offset_hz, amplitude_dbfs=0.0, noise_floor_dbfs=-120.0
        )
        spec = compute_spectrum(iq, n_fft=n_fft, sample_rate=sample_rate, center_freq=center_freq, window_name="hann")
        peak_powers.append(spec["peak_power"])

    # 峰值读数应全部在 0 dBFS 附近 (<= 0.2 dB 误差)
    for p in peak_powers:
        assert math.isclose(p, 0.0, abs_tol=0.2)
    # 各点数之间峰值差异不超过 0.15 dB
    assert max(peak_powers) - min(peak_powers) < 0.15


def test_spectrum_exponential_averaging():
    """验证频谱平滑平均能显著降低白噪声随机起伏的方差。"""
    sample_rate = 4000000.0
    center_freq = 2480000000.0
    n_fft = 1024

    prev = None
    records = []
    for i in range(25):
        # 传递不同 seed，产生真实统计独立的白噪声帧
        noise_iq = generate_test_tone_iq(
            sample_rate, n_fft, 0.0, amplitude_dbfs=-120.0, noise_floor_dbfs=-80.0, seed=100 + i
        )
        spec = compute_spectrum(
            noise_iq,
            n_fft=n_fft,
            sample_rate=sample_rate,
            center_freq=center_freq,
            previous_powers=prev,
            avg_weight=0.8,
        )
        prev = np.array(spec["powers"], dtype=np.float32)
        records.append(spec["powers"])

    # 平滑后最后一帧的方差明显小于未经平均的初始帧
    var_first = np.var(records[0])
    var_last = np.var(records[-1])
    assert var_last < var_first * 0.6


def test_spectrum_session_lifecycle_and_simulation():
    """验证通过仿真 SDR 启动、状态查询、动态调参、暂停恢复及停止。"""
    mgr = get_spectrum_manager()
    req = SpectrumStartRequest(
        sdr="sim",
        center_freq=2480000000.0,
        sample_rate=8000000.0,
        n_fft=1024,
        window="hann",
        fft_average=4,
        fps_limit=20.0,
    )

    status = mgr.start_session(req)
    assert status.session_id is not None
    assert status.state == "running"
    assert status.actual_center_freq == 2480000000.0
    assert status.actual_sample_rate == 8000000.0

    session_id = status.session_id

    # 检查状态读取
    cur = mgr.get_status()
    assert cur.session_id == session_id
    assert cur.state == "running"

    # 动态调参
    update_res = mgr.update_parameters(
        session_id,
        SpectrumUpdateRequest(
            center_freq=2450000000.0,
            fft_average=8,
            fps_limit=10.0,
        ),
    )
    assert update_res["center_freq"] == 2450000000.0
    assert update_res["fft_average"] == 8

    # 暂停与恢复显示
    pause_status = mgr.pause_session(session_id)
    assert pause_status.state == "paused"

    resume_status = mgr.resume_session(session_id)
    assert resume_status.state == "running"

    # 停止会话
    stop_status = mgr.stop_session(session_id)
    assert stop_status.state == "stopped"

    # 再次查询状态为 idle
    final_status = mgr.get_status()
    assert final_status.state == "idle"


def test_device_mutual_exclusion_with_spectrum():
    """验证实时频谱启动后独占 SDR 硬件，阻止其他收发任务，停止后正确释放。"""
    dev_mgr = get_device_manager()

    session_id = "spec_test_lock_01"
    ok, err = dev_mgr.acquire_device("hackrf", session_id)
    assert ok is True

    # 尝试由另一个任务申请同一设备
    conflict_ok, conflict_err = dev_mgr.acquire_device("hackrf", "task_rx_01")
    assert conflict_ok is False
    assert "独占锁定" in conflict_err

    # 释放频谱锁
    released = dev_mgr.release_device(session_id)
    assert any("hackrf" in r for r in released)

    # 再次申请应成功
    retry_ok, _ = dev_mgr.release_device("task_rx_01") if False else dev_mgr.acquire_device("hackrf", "task_rx_01")
    assert retry_ok is True
    dev_mgr.release_device("task_rx_01")


def test_spectrum_realtime_demod_pipeline():
    """验证频谱会话内置实时解调通道在仿真信号下能捕获并解析 FLRC 数据包。"""
    import asyncio
    import time
    from webui.backend.app.spectrum.session import SpectrumSession

    req = SpectrumStartRequest(
        sdr="sim",
        center_freq=2480000000.0,
        sample_rate=8000000.0,
        demod_enabled=True,
        protocol="dk32s",
    )
    session = SpectrumSession(req)
    session.start()
    loop = asyncio.new_event_loop()
    sub_queue = session.register_subscriber(loop)

    packets = []
    start = time.time()
    try:
        while time.time() - start < 4.0:
            loop.run_until_complete(asyncio.sleep(0.05))
            while not sub_queue.empty():
                item = sub_queue.get_nowait()
                if item.get("type") == "demod_packet":
                    packets.append(item["packet"])
            if len(packets) >= 1:
                break
    finally:
        session.stop()
        loop.close()

    assert len(packets) >= 1
    first_pkt = packets[0]
    assert first_pkt.get("crc_ok") is True
    assert first_pkt.get("application", {}).get("protocol") == "dk32s"
    assert first_pkt.get("application", {}).get("command_name") == "配对参数"


def test_spectrum_demod_parameter_hot_update():
    """验证动态切换实时解调通道及频点时不触发硬件重启。"""
    from webui.backend.app.spectrum.session import SpectrumSession

    req = SpectrumStartRequest(
        sdr="sim",
        center_freq=2480000000.0,
        sample_rate=8000000.0,
        demod_enabled=False,
    )
    session = SpectrumSession(req)
    session.start()
    try:
        update = SpectrumUpdateRequest(
            demod_enabled=True,
            demod_center_freq=2480500000.0,
            protocol="dk32s",
        )
        res = session.update_parameters(update)
        assert res["restarted"] is False
        status = session.get_status()
        assert status.demod_enabled is True
        assert status.demod_center_freq == 2480500000.0
        assert status.demod_protocol == "dk32s"
    finally:
        session.stop()
