import { clampMascot, mascotRectsOverlap } from './mascotPosition';
import type { MascotPoint, MascotRect, MascotSize, MascotViewport } from './mascotPosition';

type Side = 'left' | 'right';

/**
 * Resolve a persistent floating dock, independent of scrolling document rectangles.
 * sidebarWidth is the sidebar's absolute right edge in viewport coordinates.
 * A crowded document never changes the character's size or removes its position.
 */
export function resolveMascotDock(preferred: MascotPoint, size: MascotSize, viewport: MascotViewport, side: Side, sidebarWidth = 0): MascotPoint {
  const inset = 8;
  const left = viewport.x + inset;
  const right = Math.max(left, viewport.x + viewport.width - size.width - inset);
  const sidebarRight = Number.isFinite(sidebarWidth) ? sidebarWidth : viewport.x;
  const contentLeft = Math.min(right, Math.max(left, sidebarRight + (sidebarRight > viewport.x ? inset : 0)));
  const middle = viewport.x + viewport.width / 2;
  let minX = contentLeft;
  let maxX = right;
  if (side === 'right') minX = Math.min(right, Math.max(minX, middle + inset));
  else {
    const wholeCharacterMax = middle - size.width - inset;
    // Keep the chosen half whenever it fits. In unusually narrow content areas,
    // prefer staying visible beside navigation over disappearing or shrinking.
    maxX = Math.max(minX, Math.min(right, wholeCharacterMax));
  }
  const bounded = clampMascot({
    x: Number.isFinite(preferred.x) ? preferred.x : side === 'left' ? minX : maxX,
    y: Number.isFinite(preferred.y) ? preferred.y : viewport.y + viewport.height - size.height - 24,
  }, size, viewport, inset);
  return { x: Math.max(minX, Math.min(bounded.x, maxX)), y: bounded.y };
}

/**
 * Temporarily nudge a docked character along its current edge when it would
 * cover an interactive control. The saved position and character size stay
 * untouched, so the character returns to its chosen dock as soon as the area
 * is clear again.
 */
export function avoidMascotControls(position: MascotPoint, size: MascotSize, viewport: MascotViewport, controls: MascotRect[], gap = 8): MascotPoint {
  const desired = clampMascot(position, size, viewport, 8);
  const body = { ...desired, ...size };
  if (!controls.some(control => mascotRectsOverlap(body, control, gap))) return desired;

  const candidates = new Set<number>([
    desired.y,
    viewport.y + 8,
    viewport.y + viewport.height - size.height - 8,
  ]);
  for (const control of controls) {
    const horizontallyRelevant = desired.x < control.x + control.width + gap
      && desired.x + size.width + gap > control.x;
    if (!horizontallyRelevant) continue;
    candidates.add(control.y - size.height - gap);
    candidates.add(control.y + control.height + gap);
  }

  const ordered = [...candidates]
    .map(y => clampMascot({ x: desired.x, y }, size, viewport, 8))
    .sort((a, b) => Math.abs(a.y - desired.y) - Math.abs(b.y - desired.y));
  return ordered.find(point => !controls.some(control => mascotRectsOverlap({ ...point, ...size }, control, gap))) ?? desired;
}
