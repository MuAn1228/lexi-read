/** Geometry is kept independent of the DOM so temporary avoidance never alters saved placement. */
export type MascotPoint = { x: number; y: number };
export type MascotSize = { width: number; height: number };
export type MascotViewport = MascotPoint & MascotSize;
export type MascotRect = MascotPoint & MascotSize;
export type MascotAnchor = 'left' | 'right' | 'bottom' | 'free';

export function clampMascot(point: MascotPoint, size: MascotSize, viewport: MascotViewport, inset = 8): MascotPoint {
  const minX = viewport.x + inset;
  const minY = viewport.y + inset;
  return {
    x: Math.max(minX, Math.min(point.x, Math.max(minX, viewport.x + viewport.width - size.width - inset))),
    y: Math.max(minY, Math.min(point.y, Math.max(minY, viewport.y + viewport.height - size.height - inset))),
  };
}

export function mascotRectsOverlap(a: MascotRect, b: MascotRect, gap = 0): boolean {
  return a.x < b.x + b.width + gap && a.x + a.width + gap > b.x
    && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
}

export function snapMascot(point: MascotPoint, size: MascotSize, viewport: MascotViewport): MascotPoint & { anchor: MascotAnchor; side: 'left' | 'right' } {
  const inset = 24;
  const bounded = clampMascot(point, size, viewport, inset);
  const left = viewport.x + inset;
  const right = Math.max(left, viewport.x + viewport.width - size.width - inset);
  const bottom = Math.max(viewport.y + inset, viewport.y + viewport.height - size.height - inset);
  const threshold = Math.min(64, viewport.width * .15);
  let anchor: MascotAnchor = 'free';
  let x = bounded.x;
  let y = bounded.y;
  if (Math.abs(x - left) <= threshold) { anchor = 'left'; x = left; }
  else if (Math.abs(right - x) <= threshold) { anchor = 'right'; x = right; }
  else if (Math.abs(bottom - y) <= 48) { anchor = 'bottom'; y = bottom; }
  return { x, y, anchor, side: anchor === 'left' || x + size.width / 2 < viewport.x + viewport.width / 2 ? 'left' : 'right' };
}

/** Returns the nearest clear position, including whitespace above/below protected surfaces. */
export function findMascotPlacement(preferred: MascotPoint, size: MascotSize, viewport: MascotViewport, obstacles: MascotRect[], inset = 8): MascotPoint | null {
  const desired = clampMascot(preferred, size, viewport, inset);
  const xs = new Set([desired.x, viewport.x + inset, viewport.x + viewport.width - size.width - inset]);
  for (const rect of obstacles) {
    xs.add(rect.x - size.width - 8); xs.add(rect.x + rect.width + 8);
  }
  let best: MascotPoint | null = null;
  let bestDistance = Infinity;
  // For each horizontal boundary merge forbidden vertical intervals. This avoids a
  // cross-product of every button's x/y during high-frequency learning events.
  for (const candidateX of xs) {
    const x = clampMascot({ x: candidateX, y: desired.y }, size, viewport, inset).x;
    const blocked = obstacles.filter(rect => x < rect.x + rect.width + 4 && x + size.width + 4 > rect.x)
      .map(rect => ({ start: rect.y - size.height - 4, end: rect.y + rect.height + 4 }))
      .sort((a, b) => a.start - b.start);
    const intervals: { start: number; end: number }[] = [];
    for (const interval of blocked) {
      const previous = intervals[intervals.length - 1];
      if (previous && interval.start < previous.end) previous.end = Math.max(previous.end, interval.end);
      else intervals.push({ ...interval });
    }
    const containing = intervals.find(interval => desired.y > interval.start && desired.y < interval.end);
    const ys = containing ? [containing.start, containing.end] : [desired.y];
    for (const y of ys) {
      const point = clampMascot({ x, y }, size, viewport, inset);
      if (intervals.some(interval => point.y > interval.start && point.y < interval.end)) continue;
      const distance = (point.x - desired.x) ** 2 + (point.y - desired.y) ** 2;
      if (distance < bestDistance) { best = point; bestDistance = distance; }
    }
  }
  return best;
}
