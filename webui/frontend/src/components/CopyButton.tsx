import React, { useEffect, useRef, useState } from 'react';
import { Check, Copy, Loader2 } from 'lucide-react';

export const CopyButton: React.FC<{ text: string; label?: string }> = ({ text, label = '复制' }) => {
  const [status, setStatus] = useState<'idle' | 'pending' | 'copied' | 'error'>('idle');
  const request = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    setStatus('idle');
    return () => {
      request.current += 1;
      clearTimeout(timer.current);
    };
  }, []);

  const copy = async () => {
    const id = ++request.current;
    clearTimeout(timer.current);
    setStatus('pending');
    try {
      await navigator.clipboard.writeText(text);
      if (id !== request.current) return;
      setStatus('copied');
      timer.current = setTimeout(() => setStatus('idle'), 2000);
    } catch {
      if (id === request.current) setStatus('error');
    }
  };

  return (
    <span className="inline-flex flex-col items-end gap-1 shrink-0">
      <button
        type="button"
        onClick={copy}
        disabled={status === 'pending' || !text}
        className="px-2.5 py-1.5 rounded-lg bg-rf-panel hover:bg-rf-border text-zinc-300 text-xs font-mono flex items-center gap-1.5 border border-rf-border/80 disabled:opacity-50 transition-colors"
      >
        {status === 'copied' ? <Check className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
          : status === 'pending' ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
            : <Copy className="w-3.5 h-3.5" aria-hidden="true" />}
        <span aria-live="polite">{status === 'copied' ? '已复制' : status === 'pending' ? '正在复制…' : label}</span>
      </button>
      {status === 'error' && <span role="alert" className="max-w-56 text-xs text-red-400">复制失败，请检查浏览器剪贴板权限后重试。</span>}
    </span>
  );
};
