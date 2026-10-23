import type { TemplateItem } from '../types';
import type { RadioFrameDraft, SyncSettings } from '../stores/useRadioWorkbenchStore';

export type SyncTemplateKind = 'rf' | 'physical';

export function templateReference(value: string): string {
  return value.replace(/\\/g, '/').replace(/\/+/g, '/').replace(/^\.\//, '').replace(/^templates\//, '');
}

export function supportsSyncTemplate(template: TemplateItem, kind: SyncTemplateKind): boolean {
  const protocol = template.parsed.template?.protocol;
  if (protocol && protocol !== 'dk32s') return false;
  if (kind === 'rf') return template.category === 'rf_config' && template.applicable_commands.includes('sync');
  if (template.category !== 'physical_config' || !template.parsed.demod) return false;
  const cfg = template.parsed.demod;
  return (cfg.header_type ?? 'variable') === 'variable' && (cfg.crc_bytes ?? 3) === 3
    && (cfg.crc_initial ?? 0xFFFFFF) === 0xFFFFFF
    && (!Array.isArray(cfg.crc_initials) || cfg.crc_initials.every((seed: number) => seed === 0xFFFFFF))
    && [260000, 325000, 520000, 650000, 1040000, 1300000].includes(cfg.bit_rate ?? 325000)
    && ['1/2', '3/4', '1'].includes(cfg.coding_rate ?? '3/4')
    && [0, 0.5, 1].includes(cfg.bt ?? 1)
    && (!Array.isArray(cfg.sync_words) || cfg.sync_words.length <= 1);
}

function syncHex(value: unknown): string {
  if (typeof value !== 'string') throw new Error('模板同步字必须是十六进制字符串');
  const word = value.trim().replace(/^0x/i, '').toLowerCase();
  if (!/^[a-f\d]{8}$/.test(word)) throw new Error('模板同步字必须为 4 字节');
  return word;
}

export function syncTemplatePatch(template: TemplateItem | null, kind: SyncTemplateKind): {
  frame?: Partial<RadioFrameDraft>; sync: Partial<SyncSettings>;
} {
  if (!template) return { sync: kind === 'rf' ? { rfConfig: '' } : { physicalTemplate: '' } };
  if (!supportsSyncTemplate(template, kind)) throw new Error('此模板不适用于当前 DK32S 同步模型');
  if (kind === 'physical') {
    const cfg = template.parsed.demod;
    const word = cfg.sync_word ?? cfg.sync_words?.[0];
    return {
      frame: { protocol: 'dk32s', bitRate: cfg.bit_rate ?? 325000, codingRate: cfg.coding_rate ?? '3/4',
        headerType: 'variable', ...(word !== undefined ? { syncWord: syncHex(word) } : {}) },
      sync: { physicalTemplate: template.path, bt: cfg.bt ?? 1 },
    };
  }
  const doc = template.parsed;
  const radio = doc.radio_config ?? {};
  const table = doc.hopping_table ?? radio.hopping_table;
  const frequency = typeof table?.[0] === 'object' ? table[0].frequency_hz : table?.[0];
  if (!Number.isInteger(frequency) || frequency <= 0) throw new Error('跳频模板没有有效的初始频点');
  const entry = doc.sync_word || radio.sync_word;
  const word = typeof entry === 'object' && entry !== null
    ? entry.ota_sync_word_hex || entry.raw_hex
    : entry ?? doc.ota_sync_word_hex;
  return { frame: { syncWord: word == null ? '' : syncHex(word) },
    sync: { rfConfig: template.path, initialFreq: frequency } };
}
