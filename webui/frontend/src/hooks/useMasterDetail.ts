import { useEffect, useRef, useState } from 'react';

// CSS controls the layout; focus only moves after an explicit compact-view action.
export function useMasterDetail(initialDetailOpen: boolean) {
  const [detailOpen, setDetailOpen] = useState(initialDetailOpen);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const originRef = useRef<HTMLElement | null>(null);
  const focusTarget = useRef<'list' | 'detail' | null>(null);

  useEffect(() => {
    if (!focusTarget.current) return;
    const target = focusTarget.current;
    focusTarget.current = null;
    if (!window.matchMedia('(max-width: 1279px)').matches) return;
    if (target === 'detail') {
      workspaceRef.current?.querySelector<HTMLButtonElement>('.master-detail-back')?.focus();
    } else if (originRef.current?.isConnected) {
      originRef.current.focus();
    } else {
      workspaceRef.current?.querySelector<HTMLElement>('.master-detail-list button, .master-detail-list input')?.focus();
    }
  }, [detailOpen]);

  const openDetail = () => {
    originRef.current = document.activeElement as HTMLElement | null;
    focusTarget.current = 'detail';
    setDetailOpen(true);
  };
  const showList = () => {
    focusTarget.current = 'list';
    setDetailOpen(false);
  };
  return { detailOpen, workspaceRef, openDetail, showList };
}
