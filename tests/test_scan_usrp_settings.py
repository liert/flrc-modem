"""USRP scan readback and float-IQ regressions; all native calls are replaced."""
import ctypes as C
from math import ceil
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from flrc_modem.scan.engine import SpectrumScanner, _plan_hops
from flrc_modem.scan.models import ScanReport


def test_usrp_scan_uses_actual_rate_float_iq_and_requested_clock():
    actual_rate = 2_500_000.0
    dwell = 0.002
    weak_iq = np.full(ceil(actual_rate * dwell), 0.00001 + 0.00002j, dtype=np.complex64)
    device = MagicMock()
    device.__enter__.return_value = device
    device.configure_rx.return_value = actual_rate
    device.get_master_clock_rate.return_value = 122_880_000.0
    captured_centers = []
    analyzed = []

    def receive(sink, config, sample_count, **kwargs):
        captured_centers.append(config.frequency_hz)
        assert config.cpu_format == 'fc32'
        assert config.sample_rate == 10_000_000
        assert config.master_clock_rate == 122_880_000
        assert sample_count == round(actual_rate * dwell)
        assert kwargs['duration_s'] == dwell

        def read_frequency(handle, channel, output):
            assert channel.value == 2
            C.cast(output, C.POINTER(C.c_double))[0] = config.frequency_hz
            return 0

        device.api.dll.uhd_usrp_get_rx_freq.side_effect = read_frequency
        kwargs['rate_callback'](actual_rate)
        sink.write(weak_iq.tobytes())
        return SimpleNamespace(overflow_count=0)

    def analyze(samples, **kwargs):
        np.testing.assert_array_equal(samples, weak_iq)
        analyzed.append(kwargs)
        return ScanReport(scan_mode='offline_iq', start_freq_hz=0, stop_freq_hz=0,
                          noise_floor_dbfs=-120, threshold_db=10)

    device.receive.side_effect = receive
    scanner = SpectrumScanner()
    scanner.scan_iq_samples = MagicMock(side_effect=analyze)
    with patch('flrc_modem.sdr.usrp.library.UHDLibrary') as library, \
            patch('flrc_modem.sdr.usrp.device.USRPDevice', return_value=device) as device_class:
        report = scanner.scan_live_sdr(
            'usrp', start_freq=2_400_000_000, stop_freq=2_412_000_000,
            step_bandwidth=8_000_000, sample_rate=10_000_000, dwell_seconds=dwell,
            verify_flrc=False, sdr_options={'master_clock_rate': 122_880_000,
                                           'args': 'addr=192.168.10.2', 'channel': 2},
        )
    device_class.assert_called_once_with(library.return_value, device_args='addr=192.168.10.2',
                                         master_clock_rate=122_880_000)
    assert captured_centers == _plan_hops(2_400_000_000, 2_412_000_000, actual_rate)
    assert all(item['sample_rate'] == actual_rate for item in analyzed)
    assert [item['center_frequency'] for item in analyzed] == captured_centers
    assert report.sample_rate == actual_rate
    assert report.device_info['requested_sample_rate'] == 10_000_000
    assert report.device_info['actual_master_clock_rate'] == 122_880_000
    assert report.device_info['hops_completed'] == len(captured_centers)
    assert report.device_info['short_capture_hops'] == 0
    assert not report.device_info['partial']


def test_hop_plan_covers_both_edges_without_gaps():
    start, stop, bandwidth = 2_400_000_000, 2_483_500_000, 2_450_980.392
    hops = _plan_hops(start, stop, bandwidth)
    half_passband = bandwidth * 0.42
    assert abs(hops[0] - half_passband - start) < 0.001
    assert abs(hops[-1] + half_passband - stop) < 0.001
    assert all(right - left <= 2 * half_passband for left, right in zip(hops, hops[1:]))
