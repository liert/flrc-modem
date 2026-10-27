"""Resolve physical templates against the live receiver's actual IQ coordinates."""
from flrc_modem.config import DemodConfig, demod_config_from_values

from ..templates.manager import get_template_manager


def load_demod_template(name: str | None) -> dict | None:
    if not name:
        return None
    template = get_template_manager().get_template(name)
    if template is None:
        raise ValueError(f'物理模板不存在：{name}')
    values = template.parsed.get('demod')
    if template.category != 'physical_config' or template.format != 'toml' or not isinstance(values, dict):
        raise ValueError('实时解调需要包含 [demod] 的 physical TOML 模板')
    unknown = set(values) - DemodConfig.__dataclass_fields__.keys()
    if unknown:
        raise ValueError(f'物理模板包含未知解调参数：{", ".join(sorted(unknown))}')
    return dict(values)


def build_demod_config(request, center_freq: float, sample_rate: float, template_values: dict | None):
    values = {'chunk_seconds': 0.05}
    # Preserve legacy presets for API clients that do not select a physical template.
    if template_values is None and request.protocol == 'h12':
        values.update(bit_rate=1300000.0, coding_rate='1', bt=0.0, filter_cutoff=750000.0,
                      header_type='fixed', payload_lengths=(32, 42, 97, 127), crc_bytes=4,
                      crc_initials=(0xFFFFFFFF, 0x00000000))
    if template_values is not None:
        values.update(template_values)
    overrides = {key: getattr(request, key) for key in ('bit_rate', 'coding_rate', 'sync_word')}
    if request.crc_initial is not None:
        overrides['crc_initial'] = int(request.crc_initial, 16)
    target_freq = request.demod_center_freq or center_freq
    cutoff = values.get('filter_cutoff')
    if cutoff is None:
        bit_rate = overrides.get('bit_rate') or values.get('bit_rate', 325_000.0)
        cutoff = bit_rate * 350 / 325
    if template_values is None and abs(target_freq - center_freq) + cutoff >= sample_rate / 2:
        target_freq = center_freq
    # Recorded template sample rates, RF coordinates and file formats must not
    # relabel or retune the IQ stream already delivered by the live receiver.
    values.update(sample_rate=sample_rate, center_frequency=center_freq, frequencies=(target_freq,),
                  iq_format='cf32', max_seconds=0.0)
    return demod_config_from_values(values, overrides)
