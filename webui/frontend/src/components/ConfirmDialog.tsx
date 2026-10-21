import React, { useEffect, useRef } from 'react';

export const ConfirmDialog: React.FC<{
  titleId: string;
  descriptionId: string;
  pending: boolean;
  onCancel: () => void;
  children: React.ReactNode;
}> = ({ titleId, descriptionId, pending, onCancel, children }) => {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    // Start on the safe action; native dialog contains focus and restores it on close.
    dialog?.querySelector<HTMLButtonElement>('[data-dialog-cancel]')?.focus();
    return () => { if (dialog?.open) dialog.close(); };
  }, []);

  return (
    <dialog
      ref={ref}
      role="alertdialog"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      aria-modal="true"
      aria-busy={pending}
      onCancel={event => { event.preventDefault(); if (!pending) onCancel(); }}
      className="m-auto bg-rf-surface text-rf-text border border-rf-border rounded-xl p-6 max-w-lg w-[calc(100%_-_2rem)] max-h-[calc(100%_-_2rem)] overflow-y-auto overscroll-contain space-y-4 shadow-2xl backdrop:bg-black/75 backdrop:backdrop-blur-sm select-text"
    >
      {children}
    </dialog>
  );
};
