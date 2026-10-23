import React, { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { SpectrumFileInfo } from '../types';

type FileSelection = Pick<SpectrumFileInfo, 'path' | 'iq_format' | 'sample_rate' | 'center_freq'>;

export function SpectrumFileInput({ path, format, onChange, onUploadingChange }: {
  path: string;
  format: SpectrumFileInfo['iq_format'];
  onChange: (file: FileSelection) => void;
  onUploadingChange: (uploading: boolean) => void;
}) {
  const files = useQuery({ queryKey: ['spectrumFiles'], queryFn: api.getSpectrumFiles });
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const upload = useRef<AbortController | null>(null);

  useEffect(() => () => { upload.current?.abort(); onUploadingChange(false); }, [onUploadingChange]);

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    upload.current?.abort();
    const controller = new AbortController();
    upload.current = controller;
    setUploading(true);
    onUploadingChange(true);
    setError('');
    try {
      const result = await api.uploadSpectrumFile(file, controller.signal);
      if (controller.signal.aborted) return;
      onChange(result);
      void files.refetch();
    } catch (error) {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : '上传失败，请重试');
    } finally {
      if (!controller.signal.aborted) { setUploading(false); onUploadingChange(false); }
    }
  };

  return <div className="space-y-2 text-xs">
    <label htmlFor="spectrum-file-select" className="block text-zinc-300">已有 IQ 文件</label>
    <select id="spectrum-file-select" value={files.data?.some(file => file.path === path) ? path : ''}
      onChange={event => { const file = files.data?.find(file => file.path === event.target.value); if (file) onChange(file); }}
      className="w-full rounded border border-rf-border bg-rf-bg px-2">
      <option value="">{files.isPending ? '正在读取文件列表…' : '选择 outputs 中的采样文件'}</option>
      {files.data?.map(file => <option key={file.path} value={file.path}>{file.path}</option>)}
    </select>
    {files.isError && <p role="alert" className="text-amber-400">文件列表加载失败，可上传文件或填写路径。</p>}
    <label htmlFor="spectrum-file-upload" className="block text-zinc-300">上传 IQ 文件</label>
    <input id="spectrum-file-upload" type="file" accept=".cs8,.cu8,.cf32,.iq,.bin" disabled={uploading} onChange={handleUpload}
      className="block w-full min-w-0 text-zinc-400 file:mr-2 file:rounded file:border-0 file:bg-rf-panel file:px-2 file:py-1 file:text-xs file:text-cyan-300" />
    <label htmlFor="spectrum-iq-path" className="block text-zinc-300">IQ 文件路径</label>
    <input id="spectrum-iq-path" type="text" value={path} onChange={event => onChange({ path: event.target.value, iq_format: format })}
      placeholder="outputs/capture.cs8" spellCheck={false} className="w-full rounded border border-rf-border bg-rf-bg px-2" />
    <label htmlFor="spectrum-iq-format" className="block text-zinc-300">IQ 采样格式</label>
    <select id="spectrum-iq-format" value={format} onChange={event => onChange({ path, iq_format: event.target.value as SpectrumFileInfo['iq_format'] })}
      className="w-full rounded border border-rf-border bg-rf-bg px-2">
      <option value="cs8">cs8 · 有符号 8 位 I/Q</option>
      <option value="cu8">cu8 · 无符号 8 位 I/Q</option>
      <option value="cf32">cf32 · 32 位浮点 I/Q</option>
    </select>
    <p className="text-zinc-400 leading-relaxed">按下方采样率循环回放。已有文件自动读取伴随元数据；原始文件请确认格式、采样率和中心频率。</p>
    {uploading && <p role="status" className="text-cyan-300">正在上传 IQ 文件…</p>}
    {error && <p role="alert" className="break-words text-red-300">{error}</p>}
  </div>;
}
