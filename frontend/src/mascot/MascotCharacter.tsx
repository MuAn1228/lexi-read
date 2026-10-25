import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Check, CircleAlert, Heart, HelpCircle, MessageCircle, MoreHorizontal, Music2, Search, Smile } from 'lucide-react';
import idle from './assets/idle.png';
import studyAtlas from './assets/lexi-study-atlas-v2.png';
import actionAtlas from './assets/lexi-actions-v1.png';
import type { MascotActionAtlas, MascotAssets, MascotGesture, MascotState } from './mascotTypes';
import { MASCOT_ACTION_FRAMES, MASCOT_ATLAS_POSES, resolveMascotArtwork } from './mascotArtwork';
import { useMascotReducedMotion } from './useMascotReducedMotion';

// Study poses stay static. Explicit actions use a short, cancellable two-frame
// sequence, with the original bundled sprite as the last missing-file fallback.
const defaultAssets: MascotAssets = {
  idle,
  fallback: idle,
  pixelated: false,
  atlas: {
    src: studyAtlas, columns: 4, rows: 2, cellWidth: 384, cellHeight: 512, poses: MASCOT_ATLAS_POSES,
    poseAdjustments: { thinking: { clipRight: 12 }, explaining: { sourceOffsetX: -12 } },
  },
  actions: { src: actionAtlas, columns: 4, rows: 2, cellWidth: 384, cellHeight: 512, actions: MASCOT_ACTION_FRAMES },
};

const gestureMoods = { nod: 'smile', tilt: 'curious', hop: 'heart', stretch: 'music', pet: 'heart',
  wave: 'smile', clap: 'heart', read: 'explaining', yawn: 'music', think: 'thinking' } as const;
const stateMoods = { thinking: 'thinking', searching: 'searching', explaining: 'explaining', success: 'success', uncertain: 'curious', error: 'error' } as const;
const moodIcons = { smile: Smile, curious: HelpCircle, heart: Heart, music: Music2, thinking: MoreHorizontal, searching: Search, explaining: MessageCircle, success: Check, error: CircleAlert };

/** Schedule only the next actual pose boundary, never a render loop. */
function useActionElapsed(gesture: MascotGesture | null, action: NonNullable<MascotActionAtlas['actions'][keyof MascotActionAtlas['actions']]> | undefined,
  enabled: boolean, reducedMotion: boolean): number {
  const [time, setTime] = useState({ id: -1, elapsedMs: 0 });
  const id = gesture?.id, until = gesture?.until;
  const duration = action?.duration, frameDuration = action?.frameDuration;
  useEffect(() => {
    if (!enabled || reducedMotion || id === undefined || until === undefined || duration === undefined || frameDuration === undefined ||
        !Number.isFinite(duration) || duration <= 0 || duration > 5000 ||
        !Number.isFinite(frameDuration) || frameDuration < 100 || frameDuration > duration) return;
    let timer: number | undefined;
    const update = () => {
      const elapsedMs = Math.max(0, Date.now() - (until - duration));
      setTime({ id, elapsedMs });
      if (elapsedMs < duration) timer = window.setTimeout(update, Math.max(16, Math.min(duration - elapsedMs, frameDuration - elapsedMs % frameDuration)));
    };
    update();
    return () => window.clearTimeout(timer);
  }, [enabled, reducedMotion, id, until, duration, frameDuration]);
  return !reducedMotion && time.id === id ? time.elapsedMs : 0;
}

export function MascotCharacter({ state, reactionId, gesture, interactionEnabled, side, pressed, dragging, assets = defaultAssets }: {
  state: MascotState; reactionId: number; gesture: MascotGesture | null; interactionEnabled: boolean;
  side: 'left' | 'right'; pressed: boolean; dragging: boolean; assets?: MascotAssets;
}) {
  const [failedSources, setFailedSources] = useState<string[]>([]);
  const [readyActions, setReadyActions] = useState<string[]>([]);
  const loadingActions = useRef(new Map<string, HTMLImageElement>());
  useEffect(() => () => {
    for (const image of loadingActions.current.values()) { image.onload = null; image.onerror = null; }
    loadingActions.current.clear();
  }, []);
  const reducedMotion = useMascotReducedMotion();
  const activeGesture = interactionEnabled && !dragging ? gesture : null;
  const action = activeGesture ? assets.actions?.actions[activeGesture.kind] : undefined;
  const actionsEnabled = Boolean(activeGesture && assets.actions && !failedSources.includes(assets.actions.src) &&
    (state === 'idle' || state === 'reading'));
  const actionsSource = assets.actions?.src;
  const actionReady = Boolean(actionsSource && readyActions.includes(actionsSource));
  // Keep the already decoded study sprite visible while a new action sheet loads.
  // The sheet may finish after a cancelled gesture; it is cached, never replayed.
  useEffect(() => {
    if (!actionsEnabled || !action || !actionsSource || actionReady || loadingActions.current.has(actionsSource)) return;
    const source = actionsSource;
    const image = new Image();
    loadingActions.current.set(source, image);
    const finish = (ready: boolean) => {
      if (loadingActions.current.get(source) !== image) return;
      loadingActions.current.delete(source); image.onload = null; image.onerror = null;
      if (ready) setReadyActions(values => values.includes(source) ? values : [...values, source]);
      else setFailedSources(values => values.includes(source) ? values : [...values, source]);
    };
    image.onload = () => { void image.decode().then(() => finish(true), () => finish(false)); };
    image.onerror = () => finish(false);
    image.decoding = 'async'; image.src = source;
  }, [actionsEnabled, action, actionsSource, actionReady]);
  const elapsedMs = useActionElapsed(activeGesture, action, actionsEnabled && actionReady, reducedMotion);
  const displayedAssets = actionReady ? assets : { ...assets, actions: undefined };
  const artwork = resolveMascotArtwork({ assets: displayedAssets, state, gesture: activeGesture, interactionEnabled, dragging,
    reducedMotion, failedSources, fallbackSource: idle, elapsedMs });
  const stateMood = state in stateMoods ? stateMoods[state as keyof typeof stateMoods] : null;
  const mood = interactionEnabled && !dragging ? stateMood ?? (activeGesture ? gestureMoods[activeGesture.kind] : null) : null;
  const MoodIcon = mood ? moodIcons[mood] : null;
  const image = <img src={artwork.source} alt="Lexi 阅读助手" draggable={false}
    className={artwork.frame ? 'mascot-atlas-image' : undefined} style={artwork.frame?.style}
    data-pixelated={artwork.pixelated} data-asset={artwork.asset}
    data-action-ready={actionReady}
    data-pose={artwork.pose} data-pose-index={artwork.frame?.index}
    onError={() => {
      if (artwork.source !== idle) setFailedSources(values => values.includes(artwork.source) ? values : [...values, artwork.source]);
    }} />;
  return <span className={`mascot-character${pressed && !dragging ? ' is-pressed' : ''}`} data-testid="mascot-character" data-side={side}>
    <span key={activeGesture?.id ?? 'rest'} className="mascot-character-gesture" data-testid="mascot-gesture-layer" data-gesture={activeGesture?.kind ?? 'none'}>
      <span className="mascot-character-facing" style={{ '--mascot-facing': side === 'left' ? -1 : 1 } as CSSProperties}>
        <span className="mascot-character-breath">
          <span key={reactionId} className={`mascot-character-reaction is-${state}`}>
            {artwork.frame ? <span className="mascot-atlas-frame" style={{ aspectRatio: artwork.frame.aspectRatio, clipPath: artwork.frame.clipPath }}>{image}</span> : image}
          </span>
        </span>
      </span>
    </span>
    {MoodIcon && <span className="mascot-emotion-badge" data-testid="mascot-emotion-badge" data-mood={mood} aria-hidden="true"><MoodIcon size={13} strokeWidth={1.8} /></span>}
    <span className="mascot-state-light" data-state={state} aria-hidden="true" />
  </span>;
}
