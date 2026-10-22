import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { outputDockBounds, outputDockHeight, placeOutputDock } from '../utils/outputDock';

type Preference = { ratio: number | null; expandedRatio: number | null; docked: boolean; positionRatio: number };
type Preview = { height: number; top: number; docked: boolean };
type DragMode = 'move' | 'resize-top' | 'resize-bottom';
const storageKey = 'flrc-task-output-dock:v2';
const defaultPreference: Preference = { ratio: null, expandedRatio: null, docked: true, positionRatio: 1 };
const validRatio = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;

function loadPreference(): Preference {
  try {
    const value = JSON.parse(window.localStorage.getItem(storageKey) || 'null');
    if (value && validRatio(value.ratio)) return { ratio: value.ratio,
      expandedRatio: validRatio(value.expandedRatio) && value.expandedRatio > 0 ? value.expandedRatio : null,
      docked: value.docked !== false, positionRatio: validRatio(value.positionRatio) ? value.positionRatio : 1 };
  } catch { /* Storage is optional, including server rendering. */ }
  return defaultPreference;
}

export function useOutputDock({ enabled, defaultRatio, focused, onDragStart }: {
  enabled: boolean; defaultRatio: number; focused: boolean; onDragStart: () => void;
}) {
  const workspaceRef = useRef<HTMLDivElement>(null);
  const [preference, setPreference] = useState(loadPreference);
  const [workspaceHeight, setWorkspaceHeight] = useState(600);
  const [preview, setPreview] = useState<Preview | null>(null);
  const drag = useRef<{ pointerId: number; mode: DragMode; startY: number; startHeight: number;
    startTop: number; startDocked: boolean; available: number; value: Preview } | null>(null);
  const frame = useRef<number | null>(null);
  const clearFrame = () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  };
  useEffect(() => {
    if (!enabled) { drag.current = null; setPreview(null); return; }
    const workspace = workspaceRef.current;
    if (!workspace) return;
    const measure = () => { const height = workspace.getBoundingClientRect().height; if (height > 0) setWorkspaceHeight(height); };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(workspace);
    return () => { observer.disconnect(); drag.current = null; clearFrame(); };
  }, [enabled]);

  const bounds = outputDockBounds(workspaceHeight);
  const height = Math.min(bounds.maximum, preview?.height ?? (focused ? bounds.maximum : outputDockHeight(preference.ratio ?? defaultRatio, workspaceHeight)));
  const docked = preview?.docked ?? (focused || preference.docked);
  const top = docked ? workspaceHeight - height
    : Math.max(0, Math.min(workspaceHeight - height, preview?.top ?? preference.positionRatio * (workspaceHeight - height)));
  const collapsed = height <= bounds.collapsed;
  const save = (next: Preference) => {
    setPreference(next);
    try { window.localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* Optional layout persistence. */ }
  };
  const cancel = () => { drag.current = null; clearFrame(); setPreview(null); };
  const commit = (value: Preview, available = workspaceHeight) => {
    const currentBounds = outputDockBounds(available);
    const ratio = value.height <= currentBounds.collapsed ? 0 : value.height / available;
    const location = placeOutputDock(value.top, value.height, available, value.docked);
    save({ ratio, expandedRatio: ratio > 0 ? ratio : preference.expandedRatio ?? defaultRatio,
      docked: location.docked, positionRatio: available > value.height ? location.top / (available - value.height) : 1 });
  };
  const expand = () => {
    if (collapsed) save({ ...preference, ratio: preference.expandedRatio ?? defaultRatio });
  };
  const toggle = () => {
    if (collapsed) expand();
    else save({ ...preference, ratio: 0, expandedRatio: height / workspaceHeight, docked,
      positionRatio: docked ? 1 : top / Math.max(1, workspaceHeight - bounds.collapsed) });
  };
  const reset = () => {
    cancel(); setPreference(defaultPreference);
    try { window.localStorage.removeItem(storageKey); } catch { /* Optional layout persistence. */ }
  };
  const handlers = (mode: DragMode) => ({
    onPointerDown: (event: PointerEvent<HTMLElement>) => {
      if (event.button !== 0 || drag.current) return;
      event.preventDefault(); event.currentTarget.focus();
      const available = workspaceRef.current?.getBoundingClientRect().height || workspaceHeight;
      const value = { height, top, docked };
      drag.current = { pointerId: event.pointerId, mode, startY: event.clientY, startHeight: height,
        startTop: top, startDocked: docked, available, value };
      event.currentTarget.setPointerCapture(event.pointerId);
      setPreview(value); onDragStart();
    },
    onPointerMove: (event: PointerEvent<HTMLElement>) => {
      const current = drag.current;
      if (!current || current.pointerId !== event.pointerId) return;
      const delta = event.clientY - current.startY;
      const currentBounds = outputDockBounds(current.available);
      if (current.mode === 'move') {
        current.value = { height: current.startHeight,
          ...placeOutputDock(current.startTop + delta, current.startHeight, current.available, false) };
      } else {
        const fixedBottom = current.startTop + current.startHeight;
        const maximum = current.mode === 'resize-top' ? Math.min(currentBounds.maximum, fixedBottom) : Math.min(currentBounds.maximum, current.available - current.startTop);
        const wanted = current.startHeight + (current.mode === 'resize-top' ? -delta : delta);
        const nextHeight = wanted <= currentBounds.collapsed + 16 ? currentBounds.collapsed
          : Math.min(maximum, Math.max(Math.min(currentBounds.minimum, maximum), wanted));
        current.value = { height: nextHeight,
          top: current.mode === 'resize-top' ? fixedBottom - nextHeight : current.startTop,
          docked: current.mode === 'resize-top' && current.startDocked };
      }
      if (frame.current === null) frame.current = requestAnimationFrame(() => {
        frame.current = null; if (drag.current) setPreview({ ...drag.current.value });
      });
    },
    onPointerUp: (event: PointerEvent<HTMLElement>) => {
      const current = drag.current;
      if (!current || current.pointerId !== event.pointerId) return;
      const location = placeOutputDock(current.value.top, current.value.height, current.available);
      commit({ ...current.value, ...location }, current.available); cancel();
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    },
    onPointerCancel: cancel,
    onLostPointerCapture: cancel,
    onDoubleClick: reset,
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
      if (event.key === 'Escape') { cancel(); return; }
      if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); onDragStart();
      if (mode === 'move') {
        const wanted = event.key === 'Home' ? 0 : event.key === 'End' ? workspaceHeight - height
          : top + (event.key === 'ArrowUp' ? -16 : 16);
        commit({ height, ...placeOutputDock(wanted, height, workspaceHeight, event.key === 'End') });
      } else {
        const wanted = event.key === 'Home' ? 0 : event.key === 'End' ? bounds.maximum
          : collapsed && event.key === 'ArrowUp' ? bounds.minimum
          : height <= bounds.minimum && event.key === 'ArrowDown' ? 0 : height + (event.key === 'ArrowUp' ? 32 : -32);
        const nextHeight = outputDockHeight(wanted <= 0 ? 0 : wanted / workspaceHeight, workspaceHeight);
        commit({ height: nextHeight, top: Math.min(top, workspaceHeight - nextHeight), docked });
      }
    },
  });
  return { workspaceRef, height, top, docked, collapsed, dragging: preview !== null, workspaceHeight,
    maximum: bounds.maximum, separatorProps: handlers('resize-top'), bottomSeparatorProps: handlers('resize-bottom'),
    moveProps: handlers('move'), toggle, expand, reset };
}
