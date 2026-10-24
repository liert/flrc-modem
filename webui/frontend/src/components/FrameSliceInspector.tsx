import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { FrameSlice } from '../types';
import { ProtocolHexView, ProtocolInspectionResult } from './ProtocolInspectionResult';

export function FrameSliceInspector({ slice, protocol }: { slice: FrameSlice; protocol: string }) {
  const isPayload = slice.name === 'Payload Data';
  const inspection = useQuery({
    queryKey: ['protocolInspection', protocol, slice.hex_value],
    queryFn: ({ signal }) => api.decodeProtocol(protocol, slice.hex_value, signal),
    enabled: isPayload && Boolean(slice.hex_value),
    staleTime: 60000,
    retry: false,
  });

  return <div className="pi-slice-inspector min-w-0 space-y-3 rounded-xl border border-rf-border bg-rf-bg p-3.5">
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <span className="font-semibold text-zinc-200">{slice.name}</span>
      <span className="font-mono text-zinc-400">{slice.bits} bits ({slice.bytes} Bytes)</span>
    </div>
    <p className="text-xs text-zinc-400">{slice.description}</p>
    {isPayload && inspection.data
      ? <ProtocolInspectionResult key={protocol + ':' + slice.hex_value} result={inspection.data} layout="slice" />
      : <>
        <ProtocolHexView payloadHex={slice.hex_value} />
        {isPayload && <div className="border-t border-rf-border pt-4">
          {inspection.isError ? <div role="alert" className="flex flex-wrap items-center gap-3 text-xs text-rose-300">
            <span>载荷解析失败：{inspection.error.message}</span>
            <button type="button" disabled={inspection.isFetching} onClick={() => void inspection.refetch()} className="underline disabled:opacity-50">重试</button>
          </div> : <p role="status" className="text-xs text-zinc-400">正在解析载荷字段…</p>}
        </div>}
      </>}
  </div>;
}
