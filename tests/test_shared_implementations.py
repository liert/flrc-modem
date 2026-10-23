"""跨入口共享实现的回归：完整帧、配置、IQ、RF、命令转发和日志。"""
import io
import json
import logging
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from flrc_modem.config import demod_config_from_values, load_config
from flrc_modem.dsp import detector
from flrc_modem.io.iq import decode_samples
from flrc_modem.log import get_logger, setup_logging, shutdown_logging, wide_event
from flrc_modem.scan.flrc_verifier import verify_flrc_candidate
from flrc_modem.sync.live import parse_rf_config
from webui.backend.app.spectrum.demod_config import build_demod_config
from webui.backend.app.spectrum.dsp import convert_raw_iq_to_complex

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("payload_size", [16, 40, 127])
@pytest.mark.parametrize("jit", [True, False])
def test_scanner_and_decoder_share_full_frame_detection(payload_size, jit, monkeypatch):
    if not jit:
        monkeypatch.setattr(detector, "_scan_preambles_jit", None)
    helpers = runpy.run_path(str(ROOT / "tests/test_dsp_fastpath.py"))
    iq = helpers["_iq"](payload=bytes(range(payload_size)))
    frequency = helpers["FREQUENCY"]
    frames, groups = detector.BurstDemodulator(helpers["_config"]()).demodulate(iq, frequency)
    scan = verify_flrc_candidate(iq, helpers["SAMPLE_RATE"], frequency, frequency,
                                 350000, candidate_bit_rates=(325000,))
    assert len(frames) == scan["crc_valid_frames"] == 1
    assert groups == scan["burst_count"] == 1
    assert scan["flrc_confidence"] == 1.0
    assert scan["sync_words"][0] == frames[0].packet.sync_word.hex()


def test_scanner_keeps_preamble_evidence_without_crc(monkeypatch):
    helpers = runpy.run_path(str(ROOT / "tests/test_dsp_fastpath.py"))
    monkeypatch.setattr(detector, "decode_packet", lambda *a, **kw: None)
    frequency = helpers["FREQUENCY"]
    scan = verify_flrc_candidate(helpers["_iq"](), helpers["SAMPLE_RATE"], frequency,
                                 frequency, 350000, candidate_bit_rates=(325000,))
    assert not scan["is_flrc"] and scan["sync_words"]
    assert scan["verification_status"] == "preamble"
    assert scan["burst_count"] == 1 and scan["crc_valid_frames"] == 0
    assert scan["flrc_confidence"] == 0.65


def test_cli_and_live_template_sync_lists_have_identical_normalization(tmp_path):
    path = tmp_path / "profile.toml"
    path.write_text('[demod]\nsync_words=["ecee6698", "54455354", "ecee6698"]\ncrc_initials=[0, 16777215]\n')
    cli = load_config(path, {"crc_initial": 42})
    request = SimpleNamespace(protocol="dk32s", bit_rate=None, coding_rate=None,
                              sync_word=None, crc_initial="2a", demod_center_freq=None)
    values = {"sync_words": ["ecee6698", "54455354", "ecee6698"], "crc_initials": [0, 16777215]}
    live = build_demod_config(request, cli.center_frequency, cli.sample_rate, values)
    assert cli.sync_candidates == live.sync_candidates == (bytes.fromhex("ecee6698"), b"TEST")
    assert cli.crc_initial == live.crc_initial == 42
    assert cli.crc_initials == live.crc_initials
    assert demod_config_from_values(values).sync_candidates == cli.sync_candidates


@pytest.mark.parametrize("fmt,raw", [
    ("cs8", b"\x01\x02\x03"), ("sc16", b"\0" * 6),
    ("fc32", np.array([np.nan, 0], dtype="<f4").tobytes()),
    ("cf32", np.array([0, np.inf], dtype="<f4").tobytes()),
    ("cs8", np.array([complex(np.nan, 0)], dtype=np.complex64)),
    ("fc32", np.zeros((1, 1), dtype=np.complex64)),
])
def test_all_iq_entrypoints_reject_the_same_invalid_samples(fmt, raw):
    for decode in (decode_samples, convert_raw_iq_to_complex):
        with pytest.raises(ValueError):
            decode(raw, fmt)


@pytest.mark.parametrize("alias,canonical,raw", [
    ("sc8", "cs8", bytes([128, 127])),
    ("sc16", "cs16", np.array([-32768, 32767], dtype="<i2").tobytes()),
    ("fc32", "cf32", np.array([.25, -.5], dtype="<f4").tobytes()),
])
def test_iq_aliases_share_scaling(alias, canonical, raw):
    np.testing.assert_array_equal(convert_raw_iq_to_complex(raw, alias), decode_samples(raw, canonical))


def test_offline_rf_script_accepts_shared_formats_and_rejects_missing_file(tmp_path, capsys):
    script = runpy.run_path(str(ROOT / "scripts/run_sync_engine.py"))
    path = tmp_path / "radio.json"
    radio = {"radio_config": {"hopping_table": [2403000000, 2404000000], "sync_word": "0x54455354"}}
    path.write_text(json.dumps(radio))
    assert script["load_hopping_table"](path) == parse_rf_config(radio)[0]
    frames = tmp_path / "frames.jsonl"
    frames.write_text("")
    assert script["main"](["--frames", str(frames), "--rf-config", str(tmp_path / "missing.json")]) == 2
    assert "missing.json" in capsys.readouterr().err


@pytest.mark.parametrize("backend", ["usrp", "hackrf"])
@pytest.mark.parametrize("direction", ["rx", "tx"])
def test_legacy_iq_commands_forward_options_to_unified_cli(backend, direction, monkeypatch):
    from importlib import import_module
    legacy = import_module(f"flrc_modem.sdr.{backend}.cli")
    common = import_module(f"flrc_modem.{direction}_cli")
    captured = []
    def run(argv, *, library_factory):
        args = common.make_argument_parser().parse_args(argv)
        captured.append(args)
        assert library_factory is getattr(legacy, "UHDLibrary" if backend == "usrp" else "HackRFLibrary")
        return 17
    monkeypatch.setattr(common, "main", run)
    argv = [direction, "--frequency", "2471660000", "--sample-rate", "2500000", "--block-bytes", "4096"] if backend == "hackrf" else [direction, "--frequency", "2471660000", "--args", "addr=test"]
    argv += ["--duration", ".2", "--output", "capture.cs8"] if direction == "rx" else ["--iq", "capture.cs8", "--repeat", "3"]
    if backend == "hackrf" and direction == "tx":
        argv += ["--tx-gain", "24", "--underrun-policy", "zeros"]
    assert legacy.main(argv) == 17
    assert captured[0].sdr == backend
    if backend == "usrp":
        assert captured[0].args == "addr=test"
    elif direction == "tx":
        assert captured[0].gain == 24 and captured[0].underrun_policy == "zeros"
    else:
        assert captured[0].block_bytes == 4096


def test_nonblocking_wide_events_remain_structured_and_extra_loggers_are_detached(tmp_path):
    path = tmp_path / "events.log"
    try:
        setup_logging(file_path=path, file_format="json", stream=io.StringIO(),
                      extra_loggers=["flrc_webui"], install_guards=False)
        with wide_event("structured", answer=42):
            pass
        get_logger("flrc_webui").info("extra")
    finally:
        shutdown_logging()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["event"] == "structured" and records[0]["answer"] == 42
    assert not logging.getLogger("flrc_webui").handlers


def test_standalone_cli_flushes_scan_logs_and_preserves_json_stdout(tmp_path):
    iq = tmp_path / "zero.cf32"
    iq.write_bytes(b"\0" * (8192 * 8))
    log = tmp_path / "scan.log"
    env = dict(os.environ, FLRC_LOG_FILE=str(log), FLRC_LOG_FILE_FORMAT="json", PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-m", "flrc_modem", "scan", "--iq", str(iq),
                             "--sample-rate", "2500000", "--center-frequency", "2480000000",
                             "--iq-format", "cf32", "--no-flrc", "--json"],
                            capture_output=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr.decode("utf-8")
    json.loads(result.stdout)
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert any(record.get("event") == "scan.completed" for record in records)
    assert records[-1]["event"] == "cli_completed" and records[-1]["exit_code"] == 0


@pytest.mark.parametrize("header_type", ["variable", "fixed"])
def test_scanner_counts_distinct_bursts_and_supports_both_headers(header_type):
    from flrc_modem.modulation import ModulationConfig, iter_signal
    from flrc_modem.phy.encoder import encode_packet
    frequency, rate = 2471660000, 2500000
    packet = encode_packet(bytes(range(40)), b"TEST", header_type=header_type)
    modulation = ModulationConfig(sample_rate=rate, offset_hz=0, guard_seconds=.004)
    burst = np.concatenate(list(iter_signal(packet.bits, modulation))).astype(np.complex64)
    rng = np.random.default_rng(51)
    iq = np.tile(burst, 2)
    iq += (.02 * (rng.normal(size=len(iq)) + 1j * rng.normal(size=len(iq)))).astype(np.complex64)
    scan = verify_flrc_candidate(iq, rate, frequency, frequency, 350000, candidate_bit_rates=(325000,))
    assert scan["burst_count"] == scan["crc_valid_frames"] == 2
    assert scan["sync_words"][0] == b"TEST".hex()


def test_entrypoint_preserves_logging_owned_by_embedding_application(tmp_path):
    from flrc_modem.entrypoint import main
    from unittest.mock import patch
    path = tmp_path / "embedded.log"
    try:
        setup_logging(file_path=path, stream=io.StringIO(), install_guards=False)
        original_handlers = list(get_logger().handlers)
        with patch("flrc_modem.cli.main", return_value=0):
            assert main(["demod", "--iq", "fake.cs8"]) == 0
        assert get_logger().handlers == original_handlers
        get_logger().info("after cli")
    finally:
        shutdown_logging()
    assert "after cli" in path.read_text(encoding="utf-8")


def test_hackrf_capture_rejects_mislabelled_output_before_opening_device(tmp_path, monkeypatch):
    from flrc_modem import rx_cli
    def unexpected(*a, **kw):
        pytest.fail("invalid format must be rejected before opening hardware")
    monkeypatch.setattr(rx_cli, "HackRFLibrary", unexpected)
    output = tmp_path / "capture.cf32"
    assert rx_cli.main(["--sdr", "hackrf", "--frequency", "2471660000", "--duration", ".1",
                        "--output", str(output), "--iq-format", "cf32"]) == 2
    assert not output.exists()
