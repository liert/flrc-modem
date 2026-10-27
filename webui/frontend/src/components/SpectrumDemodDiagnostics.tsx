import React from 'react';
import type { SpectrumStatusResponse } from '../types';

export function SpectrumDemodDiagnostics({ status }: { status: SpectrumStatusResponse }) {
  return <div aria-label="实时解调诊断" className="space-y-1 text-xs text-zinc-400">
    <p className="font-mono">
      已处理 {status.demod_windows_count ?? 0} 窗 · 候选 {status.demod_candidates_count ?? 0}
      {' · '}CRC 通过 {status.demod_packets_count ?? 0} 帧 · 丢块 {status.demod_dropped_chunks ?? 0}
    </p>
    {status.demod_accelerated === false && <p className="text-amber-300">
      当前环境未启用 Numba 解调加速，处理过慢可能丢样；请补全项目依赖并重启后端。
    </p>}
    {status.demod_error_message && <p role="alert" className="break-words text-red-400">
      解调已停止，采集继续：{status.demod_error_message}。修正配置后可重新开启解调。
    </p>}
  </div>;
}
