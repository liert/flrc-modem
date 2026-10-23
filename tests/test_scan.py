"""自动盲扫与信道感知单元测试：PSD 谱分析、自适应底噪、能量聚类、FLRC 前导检验及配置导出。"""
import io
import json
from pathlib import Path
import tomllib
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from flrc_modem.io.iq import encode_samples
from flrc_modem.modulation import ModulationConfig, modulate_bits
from flrc_modem.phy.encoder import encode_packet
from flrc_modem.scan.engine import SpectrumScanner, _merge_adjacent_channels
from flrc_modem.scan.flrc_verifier import verify_flrc_candidate
from flrc_modem.scan.models import ChannelCandidate, ScanReport
from flrc_modem.scan.psd import (
    compute_psd_waterfall,
    detect_spectral_peaks,
    estimate_adaptive_noise_floor,
)
from flrc_modem.scan_cli import main as scan_cli_main


def _generate_synthetic_tone(
    sample_rate: float,
    num_samples: int,
    tone_offset_hz: float,
    amplitude: float = 0.5,
    noise_sigma: float = 0.001,
) -> np.ndarray:
    """生成带噪声的复正弦单音 IQ 信号。"""
    t = np.arange(num_samples, dtype=np.float64) / sample_rate
    phase = 2.0 * np.pi * tone_offset_hz * t
    tone = amplitude * np.exp(1j * phase)
    noise = np.random.normal(0, noise_sigma, num_samples) + 1j * np.random.normal(0, noise_sigma, num_samples)
    return (tone + noise).astype(np.complex64)


class TestSpectrumAnalysis(unittest.TestCase):
    def test_compute_psd_waterfall_single_tone(self):
        sample_rate = 10_000_000.0
        center_freq = 2_480_000_000.0
        tone_offset = 1_500_000.0  # +1.5 MHz
        num_samples = 32768
        iq = _generate_synthetic_tone(sample_rate, num_samples, tone_offset, amplitude=0.5, noise_sigma=0.001)

        freqs, max_psd, avg_psd, slices, rbw = compute_psd_waterfall(
            iq, n_fft=2048, sample_rate=sample_rate, center_freq=center_freq
        )
        self.assertEqual(len(freqs), 2048)
        self.assertEqual(len(max_psd), 2048)
        self.assertEqual(len(avg_psd), 2048)

        # 峰值位置应在 2481.5 MHz 邻域 (误差在 1 个 RBW 约 4.88 kHz 以内)
        peak_idx = int(np.argmax(max_psd))
        detected_freq = freqs[peak_idx]
        self.assertAlmostEqual(detected_freq, center_freq + tone_offset, delta=rbw * 1.5)

        # 验证自适应底噪估计
        noise_floor = estimate_adaptive_noise_floor(avg_psd)
        # noise_sigma 0.001 对应约 -60 ~ -70 dBFS 底噪
        self.assertLess(noise_floor, -50.0)

    def test_detect_spectral_peaks_multi_carrier(self):
        sample_rate = 10_000_000.0
        center_freq = 2_480_000_000.0
        num_samples = 40000

        # 生成两个载波：-2.0 MHz (强) 与 +2.5 MHz (中等)
        iq1 = _generate_synthetic_tone(sample_rate, num_samples, -2_000_000.0, amplitude=0.6, noise_sigma=0.002)
        iq2 = _generate_synthetic_tone(sample_rate, num_samples, 2_500_000.0, amplitude=0.2, noise_sigma=0.002)
        iq_total = iq1 + iq2

        freqs, max_psd, avg_psd, slices, rbw = compute_psd_waterfall(
            iq_total, n_fft=2048, sample_rate=sample_rate, center_freq=center_freq
        )
        noise_floor = estimate_adaptive_noise_floor(avg_psd)

        peaks = detect_spectral_peaks(
            freqs,
            max_psd,
            avg_psd,
            noise_floor_dbfs=noise_floor,
            threshold_db=10.0,
            dc_center_freq=center_freq,
            time_slices=slices,
        )

        self.assertEqual(len(peaks), 2)
        freq_targets = sorted([p["frequency_hz"] for p in peaks])
        self.assertAlmostEqual(freq_targets[0], center_freq - 2_000_000.0, delta=20_000.0)
        self.assertAlmostEqual(freq_targets[1], center_freq + 2_500_000.0, delta=20_000.0)

        for p in peaks:
            self.assertGreater(p["snr_db"], 10.0)
            self.assertGreater(p["bandwidth_hz"], 5_000.0)
            self.assertGreaterEqual(p["burst_count"], 1)


class TestFLRCVerification(unittest.TestCase):
    def test_verify_flrc_positive_signal(self):
        sample_rate = 10_000_000.0
        center_freq = 2_480_000_000.0
        target_freq = 2_480_500_000.0  # +500 kHz 频偏
        offset_hz = target_freq - center_freq
        sync_word = bytes.fromhex("12345678")

        # 生成合法的 FLRC 报文
        packet = encode_packet(b"TEST_PAYLOAD_FOR_SCANNER_VERIFICATION", sync_word=sync_word, coding_rate="3/4")
        mod_cfg = ModulationConfig(sample_rate=sample_rate, bit_rate=325_000.0, offset_hz=offset_hz)
        flrc_burst = modulate_bits(packet.bits, mod_cfg)

        # 放置在 0.05 秒的背景噪声中
        num_total = int(0.05 * sample_rate)
        iq = (np.random.normal(0, 0.005, num_total) + 1j * np.random.normal(0, 0.005, num_total)).astype(np.complex64)
        # 上变频叠加
        t = np.arange(len(flrc_burst), dtype=np.float64) / sample_rate
        flrc_upconverted = (flrc_burst * np.exp(2j * np.pi * offset_hz * t)).astype(np.complex64)
        start_idx = 1000
        iq[start_idx : start_idx + len(flrc_upconverted)] += flrc_upconverted

        res = verify_flrc_candidate(
            iq,
            sample_rate=sample_rate,
            center_frequency=center_freq,
            target_frequency=target_freq,
            bandwidth_hz=400_000.0,
            candidate_bit_rates=(325_000.0, 1_300_000.0),
        )

        self.assertTrue(res["is_flrc"])
        self.assertEqual(res["bit_rate"], 325_000.0)
        self.assertGreaterEqual(res["flrc_confidence"], 0.90)
        self.assertIn("12345678", res["sync_words"])
        self.assertIn("FLRC", res["protocol_hint"])

    def test_verify_flrc_negative_cw_tone(self):
        sample_rate = 10_000_000.0
        center_freq = 2_480_000_000.0
        target_freq = 2_481_000_000.0
        iq = _generate_synthetic_tone(sample_rate, 40000, 1_000_000.0, amplitude=0.5, noise_sigma=0.01)

        res = verify_flrc_candidate(
            iq,
            sample_rate=sample_rate,
            center_frequency=center_freq,
            target_frequency=target_freq,
            bandwidth_hz=30_000.0,
        )

        self.assertFalse(res["is_flrc"])
        self.assertEqual(res["flrc_confidence"], 0.0)
        self.assertIn("CW", res["protocol_hint"])

    def test_verify_flrc_negative_wideband(self):
        sample_rate = 10_000_000.0
        center_freq = 2_480_000_000.0
        target_freq = 2_480_000_000.0
        iq = np.zeros(2000, dtype=np.complex64)

        res = verify_flrc_candidate(
            iq,
            sample_rate=sample_rate,
            center_frequency=center_freq,
            target_frequency=target_freq,
            bandwidth_hz=16_000_000.0,  # 16 MHz 宽带
        )

        self.assertFalse(res["is_flrc"])
        self.assertIn("宽带", res["protocol_hint"])


class TestSpectrumScanner(unittest.TestCase):
    def test_scan_iq_samples_and_file(self, tmp_path_factory=None):
        import tempfile

        sample_rate = 10_000_000.0
        center_freq = 2_480_000_000.0
        target_freq = 2_480_600_000.0
        offset_hz = target_freq - center_freq
        sync_word = bytes.fromhex("a5b6c7d8")

        packet = encode_packet(b"TEST_SCANNER_ENGINE_PACKET", sync_word=sync_word, coding_rate="3/4")
        mod_cfg = ModulationConfig(sample_rate=sample_rate, bit_rate=325_000.0, offset_hz=offset_hz)
        flrc_burst = modulate_bits(packet.bits, mod_cfg)

        num_samples = 60000
        iq = (np.random.normal(0, 0.003, num_samples) + 1j * np.random.normal(0, 0.003, num_samples)).astype(np.complex64)
        t = np.arange(len(flrc_burst), dtype=np.float64) / sample_rate
        iq[5000 : 5000 + len(flrc_burst)] += (flrc_burst * np.exp(2j * np.pi * offset_hz * t)).astype(np.complex64)

        iq_cs8 = np.frombuffer(encode_samples(iq, "cs8"), dtype=np.int8).astype(np.float32) / 128.0
        iq_complex = (iq_cs8[0::2] + 1j * iq_cs8[1::2]).astype(np.complex64)

        scanner = SpectrumScanner()
        report = scanner.scan_iq_samples(iq_complex, sample_rate=sample_rate, center_frequency=center_freq, threshold_db=10.0)

        self.assertEqual(report.scan_mode, "offline_iq")
        self.assertGreater(len(report.channels), 0)
        flrc_ch = [c for c in report.channels if c.is_flrc]
        self.assertGreater(len(flrc_ch), 0)
        self.assertAlmostEqual(flrc_ch[0].frequency_hz, target_freq, delta=50_000.0)

        # 写入临时 IQ 文件测试 scan_iq_file
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test_scan.cs8"
            file_path.write_bytes(encode_samples(iq_complex, "cs8"))

            report_file = scanner.scan_iq_file(
                file_path, sample_rate=sample_rate, center_frequency=center_freq, duration=0.01, threshold_db=10.0
            )
            self.assertEqual(len(report_file.channels), len(report.channels))


class TestReportAndExport(unittest.TestCase):
    def test_report_toml_and_json_export(self):
        ch1 = ChannelCandidate(
            frequency_hz=2_404_380_000.0,
            bandwidth_hz=420_000.0,
            peak_power_dbfs=-35.5,
            snr_db=32.1,
            noise_floor_dbfs=-67.6,
            burst_count=12,
            is_flrc=True,
            flrc_confidence=0.95,
            sync_words=["4a65b21c", "b59a4de3"],
            bit_rate=325000.0,
            protocol_hint="FLRC (325k)",
        )
        report = ScanReport(
            scan_mode="offline_iq",
            start_freq_hz=2_400_000_000.0,
            stop_freq_hz=2_410_000_000.0,
            noise_floor_dbfs=-67.6,
            threshold_db=10.0,
            channels=[ch1],
            sample_rate=10_000_000.0,
            center_frequency_hz=2_405_000_000.0,
        )

        # 验证 JSON
        json_str = report.to_json()
        data = json.loads(json_str)
        self.assertEqual(data["total_channels"], 1)
        self.assertEqual(data["flrc_channels_count"], 1)
        self.assertEqual(data["channels"][0]["frequency_mhz"], 2404.38)

        # 验证 TOML
        toml_str = report.to_toml_config()
        cfg = tomllib.loads(toml_str)
        self.assertIn("demod", cfg)
        self.assertEqual(cfg["demod"]["sample_rate"], 10_000_000.0)
        self.assertIn(2_404_380_000.0, cfg["demod"]["frequencies"])
        self.assertEqual(cfg["demod"]["sync_word"], "4a65b21c")

        # 验证表格格式化
        table = report.format_table(use_color=False)
        self.assertIn("2404.3800", table)
        self.assertIn("FLRC (325k)", table)
        self.assertIn("4a65b21c", table)

    def test_merge_adjacent_channels(self):
        ch1 = ChannelCandidate(
            frequency_hz=2_404_000_000.0,
            bandwidth_hz=350_000.0,
            peak_power_dbfs=-40.0,
            snr_db=25.0,
            noise_floor_dbfs=-65.0,
            burst_count=5,
            is_flrc=True,
            sync_words=["11223344"],
        )
        # 邻近 hop 检测到的同一载波 (偏离 30kHz)
        ch2 = ChannelCandidate(
            frequency_hz=2_404_030_000.0,
            bandwidth_hz=380_000.0,
            peak_power_dbfs=-35.0,
            snr_db=30.0,
            noise_floor_dbfs=-65.0,
            burst_count=8,
            is_flrc=True,
            sync_words=["11223344", "aabbccdd"],
        )
        merged = _merge_adjacent_channels([ch1, ch2], tol_hz=100_000.0)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].burst_count, 13)
        self.assertEqual(merged[0].snr_db, 30.0)
        self.assertEqual(len(merged[0].sync_words), 2)


class TestScanCLI(unittest.TestCase):
    def test_cli_missing_inputs(self):
        ret = scan_cli_main([])
        self.assertEqual(ret, 2)

    def test_cli_iq_missing_center_freq(self, tmp_path=None):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".cs8") as tmp:
            ret = scan_cli_main(["--iq", tmp.name])
            self.assertEqual(ret, 2)

    def test_cli_offline_iq_success(self):
        import tempfile

        sample_rate = 10_000_000.0
        iq = _generate_synthetic_tone(sample_rate, 20000, 500_000.0, amplitude=0.4, noise_sigma=0.005)
        raw = encode_samples(iq, "cs8")

        with tempfile.NamedTemporaryFile(suffix=".cs8", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        try:
            cfg_out = tmp_path.with_suffix(".toml")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                ret = scan_cli_main([
                    "--iq", str(tmp_path),
                    "--sample-rate", str(sample_rate),
                    "--center-frequency", "2480000000",
                    "--output-config", str(cfg_out),
                    "--json",
                ])
            self.assertEqual(ret, 0)
            output = buf.getvalue()
            parsed = json.loads(output)
            self.assertEqual(parsed["scan_mode"], "offline_iq")
            self.assertTrue(cfg_out.is_file())
            self.assertIn("demod", cfg_out.read_text(encoding="utf-8"))
        finally:
            tmp_path.unlink(missing_ok=True)
            if 'cfg_out' in locals():
                cfg_out.unlink(missing_ok=True)

    def test_cli_live_sdr_mock(self):
        # 模拟 HackRF 实时步进扫频
        mock_dev = MagicMock()
        sample_rate = 10_000_000.0
        fake_iq = _generate_synthetic_tone(sample_rate, 4096, 500_000.0, amplitude=0.3, noise_sigma=0.01)
        fake_bytes = encode_samples(fake_iq, "cs8")

        def fake_receive(sink, cfg, sample_count=None, **kwargs):
            sink.write(fake_bytes)
            return MagicMock()

        mock_dev.receive.side_effect = fake_receive
        mock_dev.__enter__.return_value = mock_dev

        with patch("flrc_modem.sdr.hackrf.device.HackRFDevice", return_value=mock_dev):
            with patch("flrc_modem.sdr.hackrf.library.HackRFLibrary"):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    ret = scan_cli_main([
                        "--sdr", "hackrf",
                        "--start-freq", "2400000000",
                        "--stop-freq", "2410000000",
                        "--step-bw", "10000000",
                        "--dwell", "0.01",
                        "--json",
                    ])
                self.assertEqual(ret, 0)
                data = json.loads(buf.getvalue())
                self.assertIn("hackrf", data["scan_mode"])

    def test_cli_live_usrp_mock(self):
        # 模拟 USRP 实时步进扫频
        mock_dev = MagicMock()
        sample_rate = 10_000_000.0
        fake_iq = _generate_synthetic_tone(sample_rate, 4096, 500_000.0, amplitude=0.3, noise_sigma=0.01)
        fake_bytes = encode_samples(fake_iq, "cf32")

        def fake_receive(sink, cfg, sample_count=None, **kwargs):
            sink.write(fake_bytes)
            return MagicMock(overflow_count=0)

        mock_dev.configure_rx.return_value = sample_rate
        mock_dev.get_master_clock_rate.return_value = 125_000_000.0
        mock_dev.receive.side_effect = fake_receive
        mock_dev.__enter__.return_value = mock_dev

        with patch("flrc_modem.sdr.usrp.device.USRPDevice", return_value=mock_dev):
            with patch("flrc_modem.sdr.usrp.library.UHDLibrary"):
                buf = io.StringIO()
                with patch("sys.stdout", buf):
                    ret = scan_cli_main([
                        "--sdr", "usrp",
                        "--start-freq", "2400000000",
                        "--stop-freq", "2410000000",
                        "--step-bw", "10000000",
                        "--dwell", "0.01",
                        "--json",
                    ])
                self.assertEqual(ret, 0)
                data = json.loads(buf.getvalue())
                self.assertIn("usrp", data["scan_mode"])

    def test_cli_table_output_and_no_flrc(self):
        import tempfile

        sample_rate = 10_000_000.0
        iq = _generate_synthetic_tone(sample_rate, 20000, 500_000.0, amplitude=0.4, noise_sigma=0.005)
        raw = encode_samples(iq, "cs8")

        with tempfile.NamedTemporaryFile(suffix=".cs8", delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        try:
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                ret = scan_cli_main([
                    "--iq", str(tmp_path),
                    "--sample-rate", str(sample_rate),
                    "--center-frequency", "2480000000",
                    "--no-flrc",
                    "--no-color",
                ])
            self.assertEqual(ret, 0)
            table_out = buf.getvalue()
            self.assertIn("FLRC 盲扫与信道感知报告", table_out)
            self.assertIn("未检验 (仅能量感知)", table_out)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_cli_invalid_freq_range(self):
        ret = scan_cli_main([
            "--sdr", "hackrf",
            "--start-freq", "2500000000",
            "--stop-freq", "2400000000",
        ])
        self.assertEqual(ret, 2)


if __name__ == "__main__":
    unittest.main()
