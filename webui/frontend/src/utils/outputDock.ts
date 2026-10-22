export const OUTPUT_DOCK_COLLAPSED_HEIGHT = 57;

export function outputDockBounds(workspaceHeight: number) {
  const available = Number.isFinite(workspaceHeight) ? Math.max(0, workspaceHeight) : 0;
  const collapsed = Math.min(OUTPUT_DOCK_COLLAPSED_HEIGHT, available);
  const maximum = Math.min(available, Math.max(collapsed, available - 72));
  const minimum = Math.min(220, maximum);
  return { available, collapsed, minimum, maximum };
}

export function outputDockHeight(ratio: number, workspaceHeight: number): number {
  const bounds = outputDockBounds(workspaceHeight);
  if (ratio <= 0) return bounds.collapsed;
  return Math.min(bounds.maximum, Math.max(bounds.minimum, ratio * bounds.available));
}

export function placeOutputDock(top: number, height: number, workspaceHeight: number, magnet = true) {
  const bottom = Math.max(0, workspaceHeight - height);
  const position = Math.max(0, Math.min(bottom, top));
  const docked = magnet && bottom - position <= 20;
  return { top: docked ? bottom : position, docked };
}

