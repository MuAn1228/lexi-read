import type { MascotActionAtlas, MascotAssets, MascotAtlas, MascotGesture, MascotGestureKind, MascotState } from './mascotTypes';

export const MASCOT_ATLAS_POSES: Record<MascotState, number> = {
  idle: 0, reading: 1, thinking: 2, explaining: 3,
  searching: 4, success: 5, uncertain: 6, error: 7,
};

export const MASCOT_ACTION_FRAMES: MascotActionAtlas['actions'] = {
  wave: { frames: [0, 1], frameDuration: 400, duration: 1600 },
  clap: { frames: [2, 3], frameDuration: 200, duration: 1600 },
  read: { frames: [4, 5], frameDuration: 550, duration: 2200 },
  yawn: { frames: [6, 7], frameDuration: 500, duration: 2000 },
};

/** Learning feedback keeps its expression even if a gesture is still finishing. */
export function mascotArtworkPose(state: MascotState, gesture: MascotGesture | null,
  interactionEnabled: boolean, dragging: boolean): MascotState {
  if (!interactionEnabled || dragging || !gesture || (state !== 'idle' && state !== 'reading')) return state;
  const poses: Partial<Record<MascotGestureKind, MascotState>> = {
    tilt: 'uncertain', wave: 'explaining', clap: 'success', read: 'reading', yawn: 'idle', think: 'thinking',
  };
  return poses[gesture.kind] ?? 'success';
}

type AtlasLayout = Pick<MascotAtlas, 'src' | 'columns' | 'rows' | 'cellWidth' | 'cellHeight'>;
type FrameAdjustment = { sourceOffsetX?: number; clipRight?: number };

function atlasFrame(atlas: AtlasLayout, index: number, adjustment?: FrameAdjustment) {
  const { columns, rows, cellWidth, cellHeight } = atlas;
  if (!atlas.src || ![columns, rows].every(value => Number.isSafeInteger(value) && value > 0 && value <= 16) ||
      ![cellWidth, cellHeight].every(value => Number.isSafeInteger(value) && value > 0 && value <= 4096)) return null;
  if (!Number.isSafeInteger(index) || index < 0 || index >= columns * rows) return null;
  const column = index % columns, row = Math.floor(index / columns);
  const { sourceOffsetX = 0, clipRight = 0 } = adjustment ?? {};
  const sourceX = column * cellWidth + sourceOffsetX;
  if (!Number.isFinite(sourceOffsetX) || Math.abs(sourceOffsetX) >= cellWidth || sourceX < 0 ||
      sourceX + cellWidth > columns * cellWidth || !Number.isFinite(clipRight) || clipRight < 0 || clipRight >= cellWidth) return null;
  return {
    index, column, row, aspectRatio: `${cellWidth} / ${cellHeight}`,
    clipPath: clipRight ? `inset(0 ${clipRight / cellWidth * 100}% 0 0)` : undefined,
    style: { width: `${columns * 100}%`, height: `${rows * 100}%`, left: `${-sourceX / cellWidth * 100}%`, top: `${-row * 100}%` },
  };
}

export function mascotAtlasFrame(atlas: MascotAtlas, pose: MascotState) {
  return atlasFrame(atlas, atlas.poses[pose], atlas.poseAdjustments?.[pose]);
}

/** Two-frame actions have a hard end; reduced motion always selects one static frame. */
export function mascotActionFrame(atlas: MascotActionAtlas, kind: MascotGestureKind, elapsedMs: number, reducedMotion: boolean) {
  const action = atlas.actions[kind];
  if (!action || !action.frames.length || action.frames.length > 16 || !Number.isFinite(elapsedMs) || elapsedMs < 0 ||
      !Number.isFinite(action.duration) || action.duration <= 0 || action.duration > 5000 || elapsedMs >= action.duration ||
      !Number.isFinite(action.frameDuration) || action.frameDuration < 100 || action.frameDuration > action.duration) return null;
  if (!action.frames.every(index => Number.isSafeInteger(index) && index >= 0 && index < atlas.columns * atlas.rows)) return null;
  const step = reducedMotion ? 0 : Math.floor(elapsedMs / action.frameDuration) % action.frames.length;
  const index = action.frames[step];
  return atlasFrame(atlas, index, atlas.frameAdjustments?.[index]);
}

/** Resolve an explicit finite action, a study pose or a legacy image, with remembered failures. */
export function resolveMascotArtwork({ assets, state, gesture, interactionEnabled, dragging, reducedMotion, failedSources, fallbackSource, elapsedMs = 0 }: {
  assets: MascotAssets; state: MascotState; gesture: MascotGesture | null;
  interactionEnabled: boolean; dragging: boolean; reducedMotion: boolean;
  failedSources: readonly string[]; fallbackSource: string;
  elapsedMs?: number;
}) {
  const pose = mascotArtworkPose(state, gesture, interactionEnabled, dragging);
  if (gesture && interactionEnabled && !dragging && (state === 'idle' || state === 'reading') &&
      assets.actions && !failedSources.includes(assets.actions.src)) {
    const actionFrame = mascotActionFrame(assets.actions, gesture.kind, elapsedMs, reducedMotion);
    if (actionFrame) return { source: assets.actions.src, asset: 'action' as const, pose: gesture.kind,
      frame: actionFrame, pixelated: false };
  }
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
