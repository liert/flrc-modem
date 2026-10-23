import type { ProtocolByteRange } from '../types';

export const rangeContains = (range: ProtocolByteRange, offset: number) =>
  offset >= range.offset && offset < range.offset + range.length;

export function packetSegments(ranges: ProtocolByteRange[]): ProtocolByteRange[] {
  return ranges.filter(range => range.section !== 'business' && !['need_ack', 'is_ack'].includes(range.path));
}

export function byteRangeAt(ranges: ProtocolByteRange[], offset: number): ProtocolByteRange | undefined {
  // A byte can contain several packed fields. Prefer the narrowest raw business
  // field; selecting its exact bit range remains available in the field list.
  const priority = (range: ProtocolByteRange) =>
    range.section === 'business' ? range.derived ? 2 : 0 : range.path === 'payload_hex' ? 3 : 1;
  const compare = (left: ProtocolByteRange, right: ProtocolByteRange) =>
    priority(left) - priority(right) || left.length - right.length ||
    (left.bit_length ?? left.length * 8) - (right.bit_length ?? right.length * 8) ||
    right.path.split('.').length - left.path.split('.').length;
  let selected: ProtocolByteRange | undefined;
  for (const range of ranges) {
    if (rangeContains(range, offset) && (!selected || compare(range, selected) < 0)) selected = range;
  }
  return selected;
}

export function selectedBits(range: ProtocolByteRange): { index: number; value: number; active: boolean }[] {
  if (range.bit_length === undefined || range.length > 4) return [];
  const bytes = range.raw_hex.match(/../g) || [];
  return Array.from({ length: bytes.length * 8 }, (_, index) => ({
    index, value: (parseInt(bytes[Math.floor(index / 8)], 16) >> (index % 8)) & 1,
    active: index >= (range.bit_offset ?? 0) && index < (range.bit_offset ?? 0) + range.bit_length!,
  }));
}
