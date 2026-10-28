import React from 'react';
import type { SpectrumStatusResponse } from '../types';

export function SpectrumDemodDiagnostics({ status }: { status: SpectrumStatusResponse }) {
  return <div aria-label="实时解调诊断" className="space-y-1 text-xs text-zinc-400">
    <p className="font-mono">
      已处理 {status.demod_windows_count ?? 0} 窗 · 候选 {status.demod_candidates_count ?? 0}
      {' · '}CRC 通过 {status.demod_packets_count ?? 0} 帧 · 丢块 {status.demod_dropped_chunks ?? 0}（累计 IQ 块）
    </p>
    {status.demod_last_window_ms != null && status.demod_window_budget_ms != null ? <p className="font-mono">
      每窗 {status.demod_last_window_ms.toFixed(1)} / {status.demod_window_budget_ms.toFixed(1)} ms
      {' · '}排队 {status.demod_queue_depth ?? 0}/{status.demod_queue_capacity ?? 0} 块
      {' · '}待处理 {(status.demod_queue_buffer_ms ?? 0).toFixed(1)} ms
    </p> : null}
    {status.demod_last_window_ms != null && status.demod_window_budget_ms != null
      && status.demod_last_window_ms > status.demod_window_budget_ms ? <p className="text-amber-300">
        本窗处理耗时超过实时预算；若持续超时且丢块增长，请降低采样率、减少后台负载，或录制 IQ 后离线解调。
      </p> : null}
    {status.demod_accelerated === false && <p className="text-amber-300">
      当前环境未启用 Numba 解调加速，处理过慢可能丢样；请补全项目依赖并重启后端。
    </p>}
    {status.demod_error_message && <p role="alert" className="break-words text-red-400">
      解调已停止，采集继续：{status.demod_error_message}。修正配置后可重新开启解调。
    </p>}
  </div>;
}
