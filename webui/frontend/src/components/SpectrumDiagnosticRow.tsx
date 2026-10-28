import React from 'react';
import type { SpectrumDiagnosticEntry } from '../hooks/useSpectrumDiagnostics';

export function SpectrumDiagnosticRow({ entry }: { entry: SpectrumDiagnosticEntry }) {
  return <tr data-diagnostic-id={entry.id}
    className={entry.severity === 'error' ? 'bg-red-950/20 text-red-300' : 'bg-amber-950/20 text-amber-200'}>
    <td className="py-1.5 px-3 text-center text-zinc-500">D{entry.id}</td>
    <td className="py-1.5 px-3 text-zinc-300" title="诊断接收时间">
      {new Date(entry.time).toLocaleTimeString('zh-CN', { hour12: false })}
    </td>
    <td className="py-1.5 px-3 text-cyan-400">
      {entry.frequencyHz != null ? `${(entry.frequencyHz / 1e6).toFixed(3)} MHz` : '—'}
    </td>
    <td className="py-1.5 px-3 font-semibold whitespace-nowrap">诊断 · {entry.category}</td>
    <td colSpan={4} className="py-1.5 px-3 whitespace-pre-wrap break-words">{entry.message}</td>
  </tr>;
}
