import type { DuplexPlanDefinition, DuplexPlanItem } from '../types';

export function parseDuplexPlan(input: unknown): DuplexPlanDefinition {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('编排方案必须为 JSON 对象');
  const document = input as Record<string, unknown>;
  const value = document.plan ?? document;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('编排内容必须为 JSON 对象');
  const plan = value as Partial<DuplexPlanDefinition>;
  if ((plan.periodic !== undefined && !Array.isArray(plan.periodic)) ||
      (plan.steps !== undefined && !Array.isArray(plan.steps)) ||
      !(plan.periodic?.length || plan.steps?.length)) throw new Error('编排至少需要一个周期任务或流程步骤');
  return structuredClone({ plan_id: plan.plan_id || 'draft', periodic: plan.periodic || [], steps: plan.steps || [], cycles: plan.cycles ?? 1 });
}

export type DuplexPayloadSource = 'message' | 'payload_hex';

export function parseDuplexPayload(source: DuplexPayloadSource, content: string): Pick<DuplexPlanItem, 'message' | 'payload_hex'> {
  if (!content.trim()) throw new Error('请选择模板或填写报文内容');
  if (source === 'message') {
    const message: unknown = JSON.parse(content);
    if (!message || typeof message !== 'object' || Array.isArray(message)) throw new Error('协议报文必须为 JSON 对象');
    return { message: message as Record<string, unknown> };
  }
  const payload_hex = content.replace(/[\t\n\r\f\v ]/g, '');
  if (!/^(?:[\da-f]{2}){6,127}$/i.test(payload_hex)) throw new Error('Hex 必须包含 6..127 字节完整的十六进制数据');
  return { payload_hex };
}

export function duplexPayloadText(item: Pick<DuplexPlanItem, 'message' | 'payload_hex'>): string {
  return item.message ? JSON.stringify(item.message, null, 2) : item.payload_hex || '';
}

export function replaceDuplexPayload<T extends DuplexPlanItem>(item: T, payload: Pick<DuplexPlanItem, 'message' | 'payload_hex'>): T {
  const { message: _message, payload_hex: _hex, ...options } = item;
  return { ...options, ...payload } as T;
}

export function duplexItemSummary(item: DuplexPlanItem): string {
  const title = item.message?.template;
  if (title && typeof title === 'object' && 'title' in title && typeof title.title === 'string') return title.title;
  return item.message ? `协议报文 · ${String(item.message.command ?? '自定义')}`
    : `Hex · ${(item.payload_hex || '').replace(/\s/g, '').length / 2} B`;
}
