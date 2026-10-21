"""命令库与真实 CLI 对照；不打开 SDR，不发送射频信号。"""
import argparse
from importlib import import_module
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from flrc_modem.cli_options import demod_overrides
from flrc_modem.config import load_config
from flrc_modem.entrypoint import COMMANDS
from webui.backend.app.commands.preflight import preflight
from webui.backend.app.commands.registry import CommandRegistry
from webui.backend.app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("command_id", [name for name in COMMANDS if name != "webui"])
def test_catalog_covers_cli_commands_and_options(command_id):
    command = CommandRegistry().get(command_id)
    assert command is not None
    assert len({p.name for p in command.parameters}) == len(command.parameters)
    parser = import_module(COMMANDS[command_id]).make_argument_parser()
    flags = {p.cli_flag for p in command.parameters}
    for action in parser._actions:
        # 诊断命令的 rx/tx/sync/duplex 已有独立统一入口；webui 不启动另一个服务器。
        # stdin-jsonl 需要持续输入管道，WebUI 使用 duplex session 投递接口。
        if action.dest in ("help", "version", "ui_events", "stdin_jsonl"):
            continue
        if not action.option_strings:
            continue
        assert flags.intersection(action.option_strings), (command_id, action.option_strings)
        if isinstance(action, argparse._AppendAction):
            parameter = next(p for p in command.parameters if p.cli_flag in action.option_strings)
            assert parameter.type in ("list_number", "list_string")


@pytest.mark.anyio
async def test_api_exposes_capture_center_and_multi_frequency_form_fields():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        commands = (await client.get("/api/v1/commands")).json()
    for command_id in ("demod", "live"):
        parameters = next(c["parameters"] for c in commands if c["id"] == command_id)
        fields = {p["name"]: p for p in parameters}
        assert fields["center_frequency"]["cli_flag"] == "--center-frequency"
        assert fields["center_frequency"]["default"] is None
        assert fields["frequency"]["type"] == "list_number"
        assert fields["frequency"]["default"] is None
        assert fields["sample_rate"]["cli_flag"] == "--sample-rate"
    exchange = next(c for c in commands if c["id"] == "exchange")
    assert exchange["requires_sdr"] and not exchange["requires_usrp"]


@pytest.mark.parametrize("command_id", ["demod", "live"])
@pytest.mark.parametrize("frequencies", [2403000000, [2402500000, 2403500000], "2402500000, 2403500000"])
def test_center_and_target_overrides_resolve_real_demod_config(tmp_path, command_id, frequencies):
    template = tmp_path / "old.toml"
    template.write_text("[demod]\ncenter_frequency=2480000000\nfrequencies=[2480060000]\n"
                        "sample_rate=2000000\nfilter_cutoff=350000\nbit_rate=325000\n")
    values = dict(config=str(template), center_frequency=2403000000, frequency=frequencies)
    if command_id == "demod":
        values["iq"] = "capture.cs8"
    parser = import_module(COMMANDS[command_id]).make_argument_parser()
    argv = CommandRegistry().build_cli_argv(command_id, values, False)
    args = parser.parse_args(argv[4:])
    config = load_config(args.config, demod_overrides(args))
    assert config.center_frequency == 2403000000
    assert config.frequencies == ((2403000000,) if isinstance(frequencies, int) else (2402500000, 2403500000))
    # 只改目标频率确实越带；修复必须传入正确的中心频率，不能绕过频带校验。
    values.pop("center_frequency")
    args = parser.parse_args(CommandRegistry().build_cli_argv(command_id, values, False)[4:])
    with pytest.raises(ValueError, match="目标频率及滤波通带"):
        load_config(args.config, demod_overrides(args))


def test_repeated_payload_lengths_and_hex_crc_candidates():
    argv = CommandRegistry().build_cli_argv("demod", dict(
        iq="capture.cs8", payload_length="32， 42", crc_seed="0, 0xffffffff"), False)
    args = import_module(COMMANDS["demod"]).make_argument_parser().parse_args(argv[4:])
    assert args.payload_lengths == [32, 42]
    assert args.crc_initials == [0, 0xFFFFFFFF]
    for value in ("2403000000, NaN", [True], [float("inf")]):
        with pytest.raises(ValueError):
            CommandRegistry().build_cli_argv("demod", dict(iq="x.iq", frequency=value))


@pytest.mark.parametrize("command_id", ["modulate", "transmit", "duplex"])
def test_payload_file_replaces_default_hex_and_message_source(tmp_path, command_id):
    path = tmp_path / "payload.bin"
    path.write_bytes(bytes.fromhex("1122334455667788"))
    values = dict(payload_file=str(path), payload_hex="bad", sync="54455354")
    if command_id == "modulate":
        values["output"] = str(tmp_path / "generated.cs8")
    else:
        values.update(message_file="does-not-exist.json", frequency=2403000000)
    argv = CommandRegistry().build_cli_argv(command_id, values, False)
    assert "--payload-hex" not in argv and "--message-file" not in argv
    args = import_module(COMMANDS[command_id]).make_argument_parser().parse_args(argv[4:])
    assert args.payload_file == path
    if command_id != "modulate":
        assert preflight(command_id, argv)["payload_bytes"] == 8


def test_exchange_rx_center_and_multiple_targets_reach_cli():
    argv = CommandRegistry().build_cli_argv("exchange", dict(
        tx_serial="tx", rx_serial="rx", frequency=2403000000, rx_center_frequency=2404000000,
        rx_frequency="2403500000 2404500000", sync="54455354", payload_hex="1122334455667788",
        output_dir="outputs/exchange"))
    args = import_module(COMMANDS["exchange"]).make_argument_parser().parse_args(argv[4:])
    assert args.rx_center_frequency == 2404000000
    assert args.rx_frequency == [2403500000, 2404500000]
    assert "--ui-events" not in argv


def test_live_runs_with_retuned_center_and_template_overrides_without_opening_radio(tmp_path, monkeypatch):
    from flrc_modem import live_cli

    template = tmp_path / "old.toml"
    template.write_text("[demod]\ncenter_frequency=2480000000\nfrequencies=[2480060000]\n"
                        "sample_rate=2000000\nfilter_cutoff=350000\nbit_rate=325000\n")
    captured = []

    class Receiver:
        stats = SimpleNamespace(to_dict=lambda: {})

        def __init__(self, config, hardware, **kwargs):
            captured.append((config, hardware, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def frames(self):
            return iter(())

    monkeypatch.setattr(live_cli, "LiveReceiver", Receiver)
    argv = CommandRegistry().build_cli_argv("live", dict(
        config=str(template), center_frequency=2403000000, frequency="2402500000, 2403500000",
        max_seconds=0, duration=10), False)
    assert "--duration" not in argv
    assert live_cli.main(argv[4:]) == 0
    config, hardware, options = captured[0]
    assert hardware.frequency_hz == config.center_frequency == 2403000000
    assert config.frequencies == (2402500000, 2403500000)
    assert options["duration"] == 0


def test_demod_command_executes_with_new_capture_center(tmp_path):
    from flrc_modem.cli import main

    path = tmp_path / "capture.cs8"
    path.write_bytes(b"\0" * 20000)
    output = tmp_path / "frames.jsonl"
    argv = CommandRegistry().build_cli_argv("demod", dict(
        iq=str(path), center_frequency=2403000000, frequency=2403000000,
        sample_rate=2000000, filter_cutoff=350000, bit_rate=325000, output=str(output)), False)
    assert main(argv[4:]) == 0
    assert output.read_text() == ""


@pytest.mark.anyio
async def test_exchange_reserves_hackrf_and_cancellation_releases_it(tmp_path, monkeypatch):
    from webui.backend.app.devices import manager as devices_module
    from webui.backend.app.events import hub as hub_module
    from webui.backend.app.storage import db as db_module
    from webui.backend.app.devices.manager import DeviceManager
    from webui.backend.app.events.hub import EventHub
    from webui.backend.app.jobs.manager import JobManager
    from webui.backend.app.storage.db import Database

    devices = DeviceManager()
    monkeypatch.setattr(devices_module, "_DEVICE_MGR", devices)
    monkeypatch.setattr(db_module, "_DB", Database(tmp_path / "webui.db"))
    monkeypatch.setattr(hub_module, "_EVENT_HUB", EventHub())
    jobs = JobManager()
    # 任务排队后立即取消，不启动真实硬件子进程。
    record = await jobs.create_and_start_run("exchange", dict(
        tx_serial="tx", rx_serial="rx", frequency=2403000000, sync="54455354",
        payload_hex="1122334455667788", output_dir=str(tmp_path / "capture")))
    assert record.sdr_type == "hackrf"
    assert devices.get_lock_info("hackrf")["run_id"] == record.id
    assert devices.acquire_device("hackrf", "other")[0] is False
    assert await jobs.stop_run(record.id)
    assert devices.get_lock_info("hackrf") is None
