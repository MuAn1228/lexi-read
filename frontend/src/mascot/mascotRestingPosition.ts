import { clampMascot } from './mascotPosition';
import type { MascotPoint, MascotSize, MascotViewport } from './mascotPosition';

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
