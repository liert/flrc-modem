"""Filter recommendations must agree for templates, CLI and live demodulation."""
from pathlib import Path

import pytest

from flrc_modem.config import DemodConfig, load_config
from webui.backend.app.spectrum.demod_config import build_demod_config
from webui.backend.app.spectrum.schemas import SpectrumStartRequest


@pytest.mark.parametrize('bit_rate,cutoff', [
    (260000, 280000), (325000, 350000), (520000, 560000),
    (650000, 700000), (1040000, 1120000), (1300000, 1400000),
])
def test_omitted_filter_tracks_effective_bit_rate(tmp_path, bit_rate, cutoff):
    path = tmp_path / 'physical.toml'
    path.write_text(f'[demod]\nbit_rate = {bit_rate}\n')
    request = SpectrumStartRequest()
    live = build_demod_config(request, request.center_freq, request.sample_rate, {'bit_rate': bit_rate})
    assert live.filter_cutoff == load_config(path, {}).filter_cutoff == cutoff
    assert DemodConfig(bit_rate=bit_rate).filter_cutoff == cutoff
    # A runtime bit-rate override must be applied before calculating the cutoff.
    request = SpectrumStartRequest(bit_rate=bit_rate)
    for template in (None, {}, {'bit_rate': 325000}):
        assert build_demod_config(request, request.center_freq, request.sample_rate, template).filter_cutoff == cutoff
    assert load_config(path, {'bit_rate': 325000}).filter_cutoff == 350000


def test_explicit_filters_and_legacy_h12_preset_are_preserved():
    root = Path(__file__).resolve().parents[1]
    request = SpectrumStartRequest(protocol='h12')
    assert load_config(root / 'templates/physical/h12.toml', {}).filter_cutoff == 750000
    assert build_demod_config(request, request.center_freq, request.sample_rate, None).filter_cutoff == 750000
    assert build_demod_config(request, request.center_freq, request.sample_rate,
                              {'bit_rate': 1300000, 'filter_cutoff': 800000}).filter_cutoff == 800000


def test_no_template_edge_fallback_uses_adaptive_passband():
    request = SpectrumStartRequest(bit_rate=1300000, demod_center_freq=2483000000)
    config = build_demod_config(request, request.center_freq, request.sample_rate, None)
    assert config.filter_cutoff == 1400000
    assert config.frequencies == (request.center_freq,)
