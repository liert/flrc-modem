// Keep these choices aligned with phy/profile.py and phy/packet_format.py.
export const PHYSICAL_BIT_RATES = [260000, 325000, 520000, 650000, 1040000, 1300000];
export const PHYSICAL_CODING_RATES = ['1/2', '3/4', '1'];
export const PHYSICAL_BT = [0, 0.5, 1];
export const PHYSICAL_HEADERS = ['variable', 'fixed', 'auto'];
export const PHYSICAL_CRC_BYTES = [2, 3, 4];

type Statement = { start: number; end: number; comment: number };
type Field = Statement & { section: string; key: string; valueStart: number; valueEnd: number };

// Locate complete TOML statements, including multiline arrays and strings.
// Only scalar values are rewritten; all unrelated source and comments survive.
function statements(source: string): Statement[] {
  const result: Statement[] = [];
  let start = 0, quote = '', triple = false, depth = 0, comment = -1, inComment = false;
  for (let i = 0; i < source.length; i++) {
    const c = source[i];
    if (inComment && c !== '\n') continue;
    if (inComment) inComment = false;
    if (quote) {
      if (c === '\\' && quote === '"') { i++; continue; }
      if (c === quote && (!triple || source.slice(i, i + 3) === quote.repeat(3))) {
        if (triple) i += 2;
        quote = '';
      }
      continue;
    }
    if (c === '#' ) {
      inComment = true;
      if (depth === 0) comment = i;
      continue;
    }
    if (c === '"' || c === "'") {
      quote = c;
      triple = source.slice(i, i + 3) === c.repeat(3);
      if (triple) i += 2;
      continue;
    }
    if (c === '[' || c === '{') depth++;
    if (c === ']' || c === '}') depth--;
    if (depth < 0) throw new Error('请先补全源码中的 TOML 括号');
    if (c === '\n' && depth === 0) {
      result.push({ start, end: i + 1, comment });
      start = i + 1; comment = -1;
    }
  }
  if (quote || depth) throw new Error('请先补全源码中的 TOML 字符串或数组');
  if (start < source.length) result.push({ start, end: source.length, comment });
  return result;
}

function inspect(source: string) {
  const fields: Field[] = [];
  const tables: { name: string; start: number; end: number }[] = [];
  let section = '';
  for (const statement of statements(source)) {
    const end = statement.comment < 0 ? statement.end : statement.comment;
    const code = source.slice(statement.start, end);
    const table = code.trim();
    if (table.startsWith('[')) {
      if (tables.length) tables[tables.length - 1].end = statement.start;
      const match = /^\[\s*(?:([A-Za-z0-9_-]+)|"([^"]+)"|'([^']+)')\s*\]$/.exec(table);
      section = match ? match[1] || match[2] || match[3] : table;
      tables.push({ name: section, start: statement.end, end: source.length });
      continue;
    }
    const assignment = /^\s*([A-Za-z0-9_-]+|"[^"]+"|'[^']+')\s*=\s*/.exec(code);
    if (!assignment) continue;
    const key = assignment[1].replace(/^['"]|['"]$/g, '');
    const valueStart = statement.start + assignment[0].length;
    const valueEnd = end - source.slice(valueStart, end).match(/\s*$/)![0].length;
    fields.push({ ...statement, section, key, valueStart, valueEnd });
  }
  return { fields, tables };
}

export function patchTomlSection(source: string, section: string, values: Record<string, string | null>): string {
  const { fields, tables } = inspect(source);
  const matches = tables.filter(table => table.name === section);
  if (matches.length > 1) throw new Error(`源码存在重复的 [${section}]`);
  if (!matches.length && fields.some(field => !field.section && field.key === section)) {
    throw new Error(`请在源码中将 ${section} 行内表改为 [${section}] 后使用参数选择`);
  }
  const edits: { start: number; end: number; text: string }[] = [];
  const additions: string[] = [];
  for (const [key, value] of Object.entries(values)) {
    const current = fields.filter(field => field.section === section && field.key === key);
    if (current.length > 1) throw new Error(`源码存在重复参数 ${key}`);
    if (current.length) {
      const field = current[0];
      edits.push(value === null ? { start: field.start, end: field.end, text: '' }
        : { start: field.valueStart, end: field.valueEnd, text: value });
    } else if (value !== null) additions.push(`${key} = ${value}`);
  }
  if (additions.length) {
    const at = matches[0]?.end ?? source.length;
    const newline = source.includes('\r\n') ? '\r\n' : '\n';
    const prefix = at > 0 && source[at - 1] !== '\n' ? newline : '';
    const heading = matches.length ? '' : `[${section}]${newline}`;
    edits.push({ start: at, end: at, text: prefix + heading + additions.join(newline) + newline });
  }
  return edits.sort((a, b) => b.start - a.start).reduce(
    (text, edit) => text.slice(0, edit.start) + edit.text + text.slice(edit.end), source);
}

export function readPhysicalParameters(source: string) {
  const { fields } = inspect(source);
  const read = (key: string, fallback: string | number): string | number => {
    const found = fields.filter(field => field.section === 'demod' && field.key === key);
    if (found.length > 1) throw new Error(`源码存在重复参数 ${key}`);
    if (!found.length) return fallback;
    const field = found[0];
    const raw = source.slice(field.valueStart, field.valueEnd);
    if (raw.startsWith('"')) return JSON.parse(raw);
    if (raw.startsWith("'") && raw.endsWith("'")) return raw.slice(1, -1);
    const number = Number(raw.replace(/_/g, ''));
    if (!raw || !Number.isFinite(number)) throw new Error(`请先补全源码中的 ${key}`);
    return number;
  };
  // Check whether the source has a writable table without mutating it.
  patchTomlSection(source, 'demod', {});
  return { bit_rate: Number(read('bit_rate', 325000)), coding_rate: String(read('coding_rate', '3/4')),
    bt: Number(read('bt', 1)), header_type: String(read('header_type', 'variable')),
    crc_bytes: Number(read('crc_bytes', 3)) };
}

export type PhysicalParameter = keyof ReturnType<typeof readPhysicalParameters>;

export function changePhysicalParameter(source: string, key: PhysicalParameter, value: string): string {
  const choices: Record<PhysicalParameter, (string | number)[]> = { bit_rate: PHYSICAL_BIT_RATES,
    coding_rate: PHYSICAL_CODING_RATES, bt: PHYSICAL_BT, header_type: PHYSICAL_HEADERS, crc_bytes: PHYSICAL_CRC_BYTES };
  if (!choices[key].some(choice => String(choice) === value)) throw new Error('不支持的物理参数选项');
  const values: Record<string, string | null> = { [key]: key === 'coding_rate' || key === 'header_type'
    ? JSON.stringify(value) : value };
  if (key === 'crc_bytes' && readPhysicalParameters(source).crc_bytes !== Number(value)) {
    values.crc_initial = `0x${'FF'.repeat(Number(value))}`;
    values.crc_initials = null;
  }
  if (key === 'bit_rate') {
    const rate = Number(value);
    const sample = inspect(source).fields.find(field => field.section === 'demod' && field.key === 'sample_rate');
    if (sample && Number(source.slice(sample.valueStart, sample.valueEnd).replace(/_/g, '')) < 4 * rate) {
      values.sample_rate = String(4 * rate);
    }
    // Match the existing low-rate and H12 high-rate receiver presets.
    values.filter_cutoff = String(rate >= 1040000 ? 750000 : rate >= 520000 ? 500000 : 350000);
  }
  return patchTomlSection(source, 'demod', values);
}
