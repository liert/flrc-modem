"""报文收发统一入口：注册、CLI 参数与硬件锁回归，不进行射频收发。"""
import asyncio
from unittest.mock import MagicMock

import pytest

from flrc_modem.duplex_cli import make_argument_parser as duplex_parser, main as duplex_main
from flrc_modem.transmit_cli import make_argument_parser as transmit_parser
from flrc_modem.sync import TDDExchangeResult
from webui.backend.app.commands.registry import CommandRegistry
from webui.backend.app.devices.manager import DeviceManager
from webui.backend.app.jobs.manager import JobManager
from webui.backend.app.storage.db import Database


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_only_duplex_and_transmit_share_group_sync_stays_independent():
    commands = CommandRegistry()
    modes = sorted((command for command in commands.list_all() if command.group_id == "message_io"), key=lambda command: command.mode_order)
    assert [command.id for command in modes] == ["duplex", "transmit"]
    assert {command.group_name for command in modes} == {"报文收发"}
    assert modes[0].requires_usrp is True
    assert modes[1].requires_usrp is False
    sync = commands.get("sync")
    assert sync.group_id is None
    assert sync.name == "同步分析 (Sync Engine)"
    assert sync.pipeline_id == "pipe_sync"


@pytest.mark.parametrize("command_id,parser", [("duplex", duplex_parser), ("transmit", transmit_parser)])
def test_mode_defaults_form_valid_noninteractive_cli(command_id, parser):
    registry = CommandRegistry()
    command = registry.get(command_id)
    defaults = {parameter.name: parameter.default for parameter in command.parameters}
    argv = registry.build_cli_argv(command_id, defaults)
    args = parser().parse_args(argv[4:])
    assert args.message_file is not None
    assert argv[3] == command_id
    if command_id == "duplex":
        assert args.sdr == "usrp"
        assert args.no_wait is False
        assert args.sync_timeout == 5
        assert args.ui_events == "ndjson"
    else:
        assert args.repeat == 10
        assert "--ui-events" not in argv
        assert "--sync-timeout" not in argv


def test_duplex_web_job_requires_message_and_rejects_other_mode_flags():
    registry = CommandRegistry()
    for values in ({}, {"message_file": ""}, {"message_file": "template.json", "repeat": 10},
                   {"message_file": "template.json", "sync_timeout": 0}):
        with pytest.raises(ValueError):
            registry.build_cli_argv("duplex", values)


@pytest.mark.anyio
async def test_modes_lock_correct_hardware_without_starting_radio(tmp_path, monkeypatch):
    from webui.backend.app.storage import db as db_module
    from webui.backend.app.devices import manager as device_module
    database, devices, jobs = Database(tmp_path / "jobs.db"), DeviceManager(), JobManager()
    monkeypatch.setattr(db_module, "_DB", database)
    monkeypatch.setattr(device_module, "_DEVICE_MGR", devices)
    release = asyncio.Event()
    async def simulated_job(run_id, argv, sdr_type):
        try:
            await release.wait()
        finally:
            devices.release_device(run_id)
    monkeypatch.setattr(jobs, "_execute_process", simulated_job)
    try:
        duplex = await jobs.create_and_start_run("duplex", {"message_file": "templates/messages/dk32s_tx_get_hops.json"})
        blind = await jobs.create_and_start_run("transmit", {"message_file": "templates/messages/dk32s_tx_get_hops.json", "protocol": "dk32s", "frequency": 2480000000, "sync": "ecee6698"})
        assert duplex.sdr_type == "usrp" and blind.sdr_type == "hackrf"
        assert devices.get_lock_info("usrp")["run_id"] == duplex.id
        assert devices.get_lock_info("hackrf")["run_id"] == blind.id
        with pytest.raises(RuntimeError, match="独占锁定"):
            await jobs.create_and_start_run("sync", {"sdr": "usrp"})
    finally:
        release.set()
        await asyncio.gather(*jobs._tasks.values())
    assert devices.get_lock_info("usrp") is None
    assert devices.get_lock_info("hackrf") is None


@pytest.mark.parametrize("json_mode", [False, True])
def test_failed_reply_returns_failure_with_both_output_formats(json_mode, monkeypatch, capsys):
    transceiver = MagicMock()
    transceiver.__enter__.return_value = transceiver
    transceiver.wait_locked.return_value = True
    transceiver.send_and_wait_reply.return_value = TDDExchangeResult(
        False, b"123456", 100.6, 17, 2471660000, tx_completed=True, error="等待匹配回复超时")
    monkeypatch.setattr("flrc_modem.duplex_cli.LiveTDDTransceiver", lambda **kwargs: transceiver)
    argv = ["--payload-hex", "010203040506"] + (["--json"] if json_mode else [])
    assert duplex_main(argv) == 2
    assert "等待匹配回复超时" in capsys.readouterr().out
