import { findMascotPlacement } from './mascotPosition';
import type { MascotPoint, MascotRect, MascotSize, MascotViewport } from './mascotPosition';

type Side = 'left' | 'right';

function validGeometry(size: MascotSize, viewport: MascotViewport): boolean {
  return [viewport.x, viewport.y].every(Number.isFinite)
    && [size.width, size.height, viewport.width, viewport.height].every(value => Number.isFinite(value) && value > 0);
}

/** Ordinary avoidance stays on the user's chosen half, including the whole character. */
export function findMascotRestingPlacement(preferred: MascotPoint, size: MascotSize, viewport: MascotViewport, obstacles: MascotRect[], side: Side): MascotPoint | null {
  if (!validGeometry(size, viewport) || ![preferred.x, preferred.y].every(Number.isFinite)
    || (side !== 'left' && side !== 'right')) return null;
  const half = {
    ...viewport,
    x: viewport.x + (side === 'right' ? viewport.width / 2 : 0),
    width: viewport.width / 2,
  };
  if (size.width > half.width - 16 || size.height > half.height - 16) return null;
  return findMascotPlacement(preferred, size, half, obstacles);
}

/** A collapsed handle belongs to its physical edge; only its vertical position may change. */
export function findMascotEdgePlacement(preferredY: number, size: MascotSize, viewport: MascotViewport, obstacles: MascotRect[], side: Side): MascotPoint | null {
  const inset = 2;
  if (!validGeometry(size, viewport) || !Number.isFinite(preferredY)
    || (side !== 'left' && side !== 'right')
    || size.width > viewport.width - inset * 2 || size.height > viewport.height - inset * 2) return null;
  const x = side === 'left' ? viewport.x + inset : viewport.x + viewport.width - size.width - inset;
  // An exact-width virtual viewport makes every candidate x clamp to this same edge.
  const edgeViewport = { ...viewport, x: x - inset, width: size.width + inset * 2 };
  return findMascotPlacement({ x, y: preferredY }, size, edgeViewport, obstacles, inset);
}
