"""主时钟必须在建链阶段传入，先于 RX/TX 配置，并通过真实回读确认。"""
from unittest.mock import MagicMock

import pytest

from flrc_modem.sdr.usrp import USRPDevice, USRPConfig, UHDError
from flrc_modem.sdr.usrp.config import with_master_clock_rate
from flrc_modem.sync import LiveTDDTransceiver
from tests.usrp_fake import FakeUHDLibrary
from webui.backend.app.commands.preflight import preflight
from webui.backend.app.commands.registry import CommandRegistry


@pytest.mark.parametrize('mcr', [122880000, 125000000, 153600000])
def test_device_creation_receives_clock_and_readback_precedes_rx_and_tx(mcr):
    lib = FakeUHDLibrary()
    with USRPDevice(lib, 'addr=192.168.10.2', master_clock_rate=mcr) as device:
        expected = f'addr=192.168.10.2,master_clock_rate={mcr}'.encode()
        assert ('usrp_make', expected) in lib.dll.calls
        assert device.device_info['master_clock_rate'] == mcr
        assert device.device_info['requested_master_clock_rate'] == mcr
        config = USRPConfig(2471660000, sample_rate=mcr / 50, master_clock_rate=mcr)
        assert device.configure_tx(config) == mcr / 50
        assert device.configure_rx(config) == mcr / 50
        checked = lib.dll.calls.index(('get_master_clock_rate', mcr))
        assert checked < lib.dll.calls.index(('set_tx_rate', mcr / 50, 0))
        assert checked < lib.dll.calls.index(('set_rx_rate', mcr / 50, 0))
        # 已在 make 阶段设置，不再依赖对 N310 无效的运行期切换。
        assert not any(isinstance(call, tuple) and call[0] == 'set_master_clock_rate' for call in lib.dll.calls)


def test_ignored_startup_clock_fails_before_any_stream_or_rate_configuration():
    lib = FakeUHDLibrary()
    lib.dll.ignore_mcr_init = True
    device = USRPDevice(lib, 'addr=192.168.10.2', master_clock_rate=122880000)
    with pytest.raises(UHDError, match=r'请求=1.2288e\+08.*实际=1.25e\+08'):
        device.open()
    assert device.handle.value is None
    assert 'usrp_free' in lib.dll.calls
    assert not any(isinstance(call, tuple) and call[0] in ('set_tx_rate', 'set_rx_rate', 'get_tx_stream', 'get_rx_stream') for call in lib.dll.calls)


def test_successful_but_ignored_runtime_set_is_not_cached_as_success():
    lib = FakeUHDLibrary()
    lib.dll.ignore_mcr_set = True
    with USRPDevice(lib) as device:
        for _ in range(2):
            with pytest.raises(UHDError, match='主时钟未生效'):
                device.set_master_clock_rate(122880000)
        assert device._master_clock_rate == 125000000
        assert lib.dll.calls.count(('set_master_clock_rate', 122880000)) == 2


def test_explicit_clock_overrides_args_once_and_manual_args_are_honored():
    merged = with_master_clock_rate('addr=192.168.10.2,master_clock_rate=125e6,type=n3xx', 122880000)
    assert merged == 'addr=192.168.10.2,type=n3xx,master_clock_rate=122880000'
    lib = FakeUHDLibrary()
    with USRPDevice(lib, 'addr=192.168.10.2,master_clock_rate=122.88e6') as device:
        assert device.get_master_clock_rate() == 122880000


def test_duplex_initial_tx_uses_selected_clock_before_rx_starts(monkeypatch):
    lib = FakeUHDLibrary()
    monkeypatch.setattr('flrc_modem.sdr.usrp.device.UHDLibrary', lambda: lib)
    trx = LiveTDDTransceiver(usrp_args='addr=192.168.10.2', master_clock_rate=122880000, sample_rate=2457600)
    device = trx.device
    trx.session.start = MagicMock()
    trx.session.stop = MagicMock()
    trx.session.events = lambda: iter(())
    try:
        trx.start()
        trx.session.start.assert_called_once()
        assert trx._tx_config(trx.frequency_hz).master_clock_rate == 122880000
        assert lib.dll.mcr == 122880000
        assert lib.dll.tx_rate == 2457600
        assert device._burst_rate == 2457600
        assert ('usrp_make', b'addr=192.168.10.2,master_clock_rate=122880000') in lib.dll.calls
    finally:
        trx.stop()
        device.close()


def test_duplex_preflight_shows_clock_in_actual_device_creation_arguments():
    argv = CommandRegistry().build_cli_argv('duplex', dict(session=True,
        args='addr=192.168.10.2', master_clock_rate=122880000, sample_rate=2457600))
    planned = preflight('duplex', argv)
    assert planned['device']['selection'] == 'addr=192.168.10.2,master_clock_rate=122880000'
    assert planned['sample_rate'] == 2457600


@pytest.mark.parametrize('text', ['nan', 'inf', '-1', '0'])
def test_invalid_clock_in_device_args_is_rejected_before_open(text):
    lib = FakeUHDLibrary()
    with pytest.raises(ValueError, match='master_clock_rate'):
        USRPDevice(lib, f'addr=192.168.10.2,master_clock_rate={text}')
    assert not lib.dll.calls
