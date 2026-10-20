import type { QueryClient } from '@tanstack/react-query';
import type { AnalysisContext, ArtifactItem, FileState, UIEvent } from '../types';

export function fileStateLabel(state?: FileState): string {
  return state ? ({ available: '可用', missing: '已删除或移动', overwritten: '已覆盖', modified: '已修改',
    pending: '尚未生成', unverified: '版本未确认', not_configured: '未配置' })[state.status] : '';
}

export function fileReadable(state?: FileState): boolean {
  return state?.readable !== false;
}

export function fileKey(path: string): string {
  const key = path.replace(/\\/g, '/').replace(/\/+/g, '/').replace(/^\.\//, '');
  return /^[a-z]:\//i.test(key) ? key.toLowerCase() : key;
}

export function analysisFiles(selectedPath: string | null, context: AnalysisContext | undefined, artifacts: ArtifactItem[] = []) {
  // 任务的输入与输出始终成对使用，产物选择不能替换其中任意一端。
  if (context) return { path: context.iq_path || '', framesPath: context.frames_path || '' };
  const same = (a?: string | null, b?: string | null) => !!a && !!b && fileKey(a) === fileKey(b);
  const path = selectedPath || artifacts.find(item => /\.(iq|cs8|cu8|cf32)$/i.test(item.path))?.path || '';
  if (/\.jsonl$/i.test(path)) return { path, framesPath: path };
  const owner = artifacts.find(item => same(item.path, path));
  return { path, framesPath: owner
    ? artifacts.find(item => item.run_id === owner.run_id && /\.jsonl$/i.test(item.path))?.path || '' : '' };
}

export function refreshForEvent(client: Pick<QueryClient, 'invalidateQueries'>, event: UIEvent): void {
  if (event.type === 'task.updated' || event.type === 'task.deleted') {
    const keys = event.type === 'task.updated' ? [['runs'], ['run', event.run_id]]
      : [['runs'], ['artifacts'], ['analysisContext']];
    for (const queryKey of keys) void client.invalidateQueries({ queryKey });
    return;
  }
  if (!['artifact.created', 'task.completed', 'task.state'].includes(event.type)) return;
  const keys = [['runs'], ['run', event.run_id], ['artifacts'], ['runArtifacts', event.run_id],
    ...(event.type === 'artifact.created' ? [['analysisContext'], ['frames'], ['spectrum'], ['constellation']]
      : [['analysisContext', event.run_id], ['frames', event.run_id]])];
  for (const queryKey of keys) void client.invalidateQueries({ queryKey });
}
