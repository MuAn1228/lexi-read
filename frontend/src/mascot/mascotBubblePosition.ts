import { findMascotPlacement, mascotRectsOverlap } from './mascotPosition';
import type { MascotPoint, MascotRect, MascotSize, MascotViewport } from './mascotPosition';

/** The bubble's position relative to the character; its tail faces the opposite edge. */
export type MascotBubbleSide = 'top' | 'bottom' | 'left' | 'right';
export type MascotBubblePlacement = {
  character: MascotPoint; bubble: MascotPoint; side: MascotBubbleSide; tailOffset: number;
};
type Options = {
  character: MascotPoint; characterSize: MascotSize; bubbleSize: MascotSize;
  viewport: MascotViewport; obstacles: MascotRect[]; inset?: number; gap?: number;
};
const sides: readonly MascotBubbleSide[] = ['top', 'bottom', 'left', 'right'];
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, max));

/** Keep a measured bubble attached, temporarily moving the pair only when no nearby position fits. */
export function findMascotBubblePlacement({ character, characterSize, bubbleSize, viewport, obstacles, inset = 12, gap = 10 }: Options): MascotBubblePlacement | null {
  if (![character.x, character.y, viewport.x, viewport.y, inset, gap].every(Number.isFinite)
    || ![characterSize.width, characterSize.height, bubbleSize.width, bubbleSize.height, viewport.width, viewport.height].every(value => Number.isFinite(value) && value > 0)
    || inset < 0 || gap < 0) return null;
  const inside = (rect: MascotRect) => rect.x >= viewport.x + inset - .001 && rect.y >= viewport.y + inset - .001
    && rect.x + rect.width <= viewport.x + viewport.width - inset + .001
    && rect.y + rect.height <= viewport.y + viewport.height - inset + .001;
  const clear = (rect: MascotRect) => inside(rect) && obstacles.every(obstacle => !mascotRectsOverlap(rect, obstacle, 4));
  const candidate = (point: MascotPoint, bubble: MascotPoint, side: MascotBubbleSide): MascotBubblePlacement | null => {
    if (!clear({ ...point, ...characterSize }) || !clear({ ...bubble, ...bubbleSize })) return null;
    const horizontal = side === 'top' || side === 'bottom';
    const origin = horizontal ? bubble.x : bubble.y;
    const span = horizontal ? bubbleSize.width : bubbleSize.height;
    const characterStart = horizontal ? point.x : point.y;
    const characterSpan = horizontal ? characterSize.width : characterSize.height;
    const padding = Math.min(14, span / 3);
    const target = clamp(characterStart + characterSpan / 2, origin + padding, origin + span - padding);
    // A clamped arrow must still point at some part of the character, not into empty space.
    if (target < characterStart || target > characterStart + characterSpan) return null;
    return { character: { ...point }, bubble, side, tailOffset: target - origin };
  };

  // Keep the perpendicular distance fixed. Along its facing edge the bubble may
  // slide to an obstacle boundary, provided its tail still intersects the character.
  for (const side of sides) {
    const horizontal = side === 'top' || side === 'bottom';
    const aligned = horizontal
      ? [character.x + (characterSize.width - bubbleSize.width) / 2, character.x, character.x + characterSize.width - bubbleSize.width]
      : [character.y + (characterSize.height - bubbleSize.height) / 2, character.y, character.y + characterSize.height - bubbleSize.height];
    const min = horizontal ? viewport.x + inset : viewport.y + inset;
    const max = horizontal ? viewport.x + viewport.width - bubbleSize.width - inset : viewport.y + viewport.height - bubbleSize.height - inset;
    const boundaries = obstacles.flatMap(rect => horizontal
      ? [rect.x - bubbleSize.width - 4, rect.x + rect.width + 4]
      : [rect.y - bubbleSize.height - 4, rect.y + rect.height + 4]);
    const starts = [...new Set([...aligned, min, max, ...boundaries].map(value => clamp(value, min, max)))];
    starts.sort((a, b) => Math.abs(a - aligned[0]) - Math.abs(b - aligned[0]));
    for (const start of starts) {
      const bubble = horizontal ? {
        x: clamp(start, viewport.x + inset, viewport.x + viewport.width - bubbleSize.width - inset),
        y: side === 'top' ? character.y - gap - bubbleSize.height : character.y + characterSize.height + gap,
      } : {
        x: side === 'left' ? character.x - gap - bubbleSize.width : character.x + characterSize.width + gap,
        y: clamp(start, viewport.y + inset, viewport.y + viewport.height - bubbleSize.height - inset),
      };
      const placement = candidate(character, bubble, side);
      if (placement) return placement;
    }
  }

  // Find the nearest free bounding bundle. Empty corners remain protected as a conservative fallback.
  let best: MascotBubblePlacement | null = null;
  let bestDistance = Infinity;
  for (const side of sides) {
    const vertical = side === 'top' || side === 'bottom';
    const width = vertical ? Math.max(characterSize.width, bubbleSize.width) : characterSize.width + gap + bubbleSize.width;
    const height = vertical ? characterSize.height + gap + bubbleSize.height : Math.max(characterSize.height, bubbleSize.height);
    if (width > viewport.width - inset * 2 || height > viewport.height - inset * 2) continue;
    const characterOffset = vertical
      ? { x: (width - characterSize.width) / 2, y: side === 'top' ? bubbleSize.height + gap : 0 }
      : { x: side === 'left' ? bubbleSize.width + gap : 0, y: (height - characterSize.height) / 2 };
    const bubbleOffset = vertical
      ? { x: (width - bubbleSize.width) / 2, y: side === 'bottom' ? characterSize.height + gap : 0 }
      : { x: side === 'right' ? characterSize.width + gap : 0, y: (height - bubbleSize.height) / 2 };
    const origin = findMascotPlacement({ x: character.x - characterOffset.x, y: character.y - characterOffset.y }, { width, height }, viewport, obstacles, inset);
    if (!origin) continue;
    const point = { x: origin.x + characterOffset.x, y: origin.y + characterOffset.y };
    const placement = candidate(point, { x: origin.x + bubbleOffset.x, y: origin.y + bubbleOffset.y }, side);
    if (!placement) continue;
    const distance = (point.x - character.x) ** 2 + (point.y - character.y) ** 2;
    if (distance < bestDistance) { best = placement; bestDistance = distance; }
  }
  return best;
}
