import React, { lazy, Suspense, useEffect, useRef } from 'react';
import { HardDrive, X } from 'lucide-react';
import { useWorkstationStore } from '../stores/useWorkstationStore';

const DeviceHealthDashboard = lazy(() => import('./DeviceHealthDashboard'));

export const DeviceDiagnosticsDialog: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog?.showModal();
    dialog?.querySelector<HTMLButtonElement>('[data-diagnostics-close]')?.focus();
    return () => {
      if (dialog?.open) dialog.close();
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);

  return (
    <dialog
      ref={ref}
      id="device-diagnostics-drawer"
      aria-labelledby="device-diagnostics-title"
      aria-describedby="device-diagnostics-description"
      aria-modal="true"
      onCancel={event => { event.preventDefault(); onClose(); }}
      onClick={event => {
        if (event.target !== event.currentTarget) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
      }}
      className="fixed inset-y-0 left-auto right-0 m-0 h-dvh max-h-none w-full max-w-3xl overflow-hidden border-0 border-l border-rf-border bg-rf-bg p-0 text-rf-text shadow-2xl backdrop:bg-black/60"
    >
      <div className="flex h-full min-h-0 flex-col">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-rf-border bg-rf-surface px-4 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <HardDrive className="h-5 w-5 shrink-0 text-cyan-400" aria-hidden="true" />
            <div>
              <h2 id="device-diagnostics-title" className="text-sm font-semibold">设备与诊断</h2>
              <p id="device-diagnostics-description" className="mt-0.5 text-xs text-zinc-400">设备连接、驱动状态与资源占用</p>
            </div>
          </div>
          <button type="button" data-diagnostics-close aria-label="关闭设备与诊断" onClick={onClose} className="shrink-0 rounded-md p-2 text-zinc-400 transition-colors hover:bg-rf-panel hover:text-zinc-100">
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </header>
        <div className="min-h-0 flex-1">
          <Suspense fallback={<div role="status" className="p-4 text-sm text-zinc-400">正在加载设备诊断…</div>}>
            <DeviceHealthDashboard />
          </Suspense>
        </div>
      </div>
    </dialog>
  );
};

export const DeviceDiagnosticsDrawer: React.FC = () => {
  const open = useWorkstationStore(state => state.deviceDiagnosticsOpen);
  const close = useWorkstationStore(state => state.closeDeviceDiagnostics);
  return open ? <DeviceDiagnosticsDialog onClose={close} /> : null;
};
