import { useCallback, useEffect, useRef, useState } from 'react';
import type { SpectrumStatusResponse } from '../types';

export interface SpectrumDiagnosticEntry {
  id: number;
  time: number;
  frequencyHz: number | null;
  category: string;
  severity: 'warning' | 'error';
  message: string;
}

// Observe recognized status changes even while the diagnostic rows are hidden.
export function useSpectrumDiagnostics(status: SpectrumStatusResponse, paused: boolean) {
  const [entries, setEntries] = useState<SpectrumDiagnosticEntry[]>([]);
  const previous = useRef({ sessionId: status.session_id, signatures: new Set<string>() });
  const sequence = useRef(0);
  const frequencyHz = status.demod_center_freq ?? status.actual_center_freq ?? null;
  const { session_id: sessionId, demod_signal_hint: hint, demod_error_message: demodError,
    demod_accelerated: accelerated, error_code: errorCode, error_message: captureError } = status;

  useEffect(() => {
    if (paused) return;
    const messages: Pick<SpectrumDiagnosticEntry, 'category' | 'severity' | 'message'>[] = [];
    if (hint) messages.push({ category: '信号识别', severity: 'warning', message: hint });
    if (demodError) messages.push({ category: '解调异常', severity: 'error',
      message: `解调已停止，采集继续：${demodError}。修正配置后可重新开启解调。` });
    if (accelerated === false) messages.push({ category: '解调性能', severity: 'warning',
      message: '当前环境未启用 Numba 解调加速，处理过慢可能丢样；请补全项目依赖并重启后端。' });
    if (captureError) messages.push({ category: '采集异常', severity: 'error',
      message: `${errorCode ? `${errorCode}：` : ''}${captureError}` });

    const active = new Set<string>();
    const added: SpectrumDiagnosticEntry[] = [];
    for (const message of messages) {
      const signature = JSON.stringify([frequencyHz, message.category, message.message]);
      active.add(signature);
      if (previous.current.sessionId !== sessionId || !previous.current.signatures.has(signature)) {
        added.push({ ...message, id: ++sequence.current, time: Date.now(), frequencyHz });
      }
    }
    previous.current = { sessionId, signatures: active };
    if (added.length) setEntries(current => [...added.reverse(), ...current].slice(0, 500));
  }, [sessionId, frequencyHz, hint, demodError, accelerated, errorCode, captureError, paused]);

  // Keep active signatures so clearing does not immediately re-add a stale hint.
  const clear = useCallback(() => setEntries([]), []);
  return { entries, clear };
}
