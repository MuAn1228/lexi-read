import type { MascotAssets, MascotAtlas, MascotGesture, MascotState } from './mascotTypes';

export const MASCOT_ATLAS_POSES: Record<MascotState, number> = {
  idle: 0, reading: 1, thinking: 2, explaining: 3,
  searching: 4, success: 5, uncertain: 6, error: 7,
};

/** Learning feedback keeps its expression even if a gesture is still finishing. */
export function mascotArtworkPose(state: MascotState, gesture: MascotGesture | null,
  interactionEnabled: boolean, dragging: boolean): MascotState {
  if (!interactionEnabled || dragging || !gesture || (state !== 'idle' && state !== 'reading')) return state;
  return gesture.kind === 'tilt' ? 'uncertain' : 'success';
}

export function mascotAtlasFrame(atlas: MascotAtlas, pose: MascotState) {
  const { columns, rows, cellWidth, cellHeight } = atlas;
  if (!atlas.src || ![columns, rows].every(value => Number.isSafeInteger(value) && value > 0 && value <= 16) ||
      ![cellWidth, cellHeight].every(value => Number.isSafeInteger(value) && value > 0 && value <= 4096)) return null;
  const index = atlas.poses[pose];
  if (!Number.isSafeInteger(index) || index < 0 || index >= columns * rows) return null;
  const column = index % columns, row = Math.floor(index / columns);
  const { sourceOffsetX = 0, clipRight = 0 } = atlas.poseAdjustments?.[pose] ?? {};
  const sourceX = column * cellWidth + sourceOffsetX;
  if (!Number.isFinite(sourceOffsetX) || Math.abs(sourceOffsetX) >= cellWidth || sourceX < 0 ||
      sourceX + cellWidth > columns * cellWidth || !Number.isFinite(clipRight) || clipRight < 0 || clipRight >= cellWidth) return null;
  return {
    index, column, row, aspectRatio: `${cellWidth} / ${cellHeight}`,
    clipPath: clipRight ? `inset(0 ${clipRight / cellWidth * 100}% 0 0)` : undefined,
    style: { width: `${columns * 100}%`, height: `${rows * 100}%`, left: `${-sourceX / cellWidth * 100}%`, top: `${-row * 100}%` },
  };
}

/** Resolve a static pose or a legacy image; a broken atlas is not retried per state. */
export function resolveMascotArtwork({ assets, state, gesture, interactionEnabled, dragging, reducedMotion, failedSources, fallbackSource }: {
  assets: MascotAssets; state: MascotState; gesture: MascotGesture | null;
  interactionEnabled: boolean; dragging: boolean; reducedMotion: boolean;
  failedSources: readonly string[]; fallbackSource: string;
}) {
  const pose = mascotArtworkPose(state, gesture, interactionEnabled, dragging);
  const frame = assets.atlas ? mascotAtlasFrame(assets.atlas, pose) : null;
  if (frame && assets.atlas && !failedSources.includes(assets.atlas.src)) {
    return { source: assets.atlas.src, asset: 'atlas' as const, pose, frame, pixelated: false };
  }
  const fallback = assets.fallback ?? fallbackSource;
  const stateSource = state === 'thinking' || state === 'searching' || state === 'uncertain' ? assets.thinking
    : state === 'success' || state === 'explaining' ? assets.happy : state === 'error' ? assets.error : assets.idle;
  // A static atlas has its own pet expression. Explicit legacy GIFs remain opt-in,
  // and reduced motion never places one in an image src.
  const petSource = interactionEnabled && !dragging && gesture?.kind === 'pet' && !reducedMotion ? assets.pet : undefined;
  const source = [petSource, stateSource, assets.idle, fallback, fallbackSource]
    .find((candidate): candidate is string => Boolean(candidate) && !failedSources.includes(candidate!)) ?? fallbackSource;
  return {
    source, asset: source === petSource ? 'pet' as const
      : source === fallback || source === fallbackSource ? 'fallback' as const : 'portrait' as const,
    pose: state, frame: null, pixelated: source === fallback || source === fallbackSource || assets.pixelated !== false,
  };
}
