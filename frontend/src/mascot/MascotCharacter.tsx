import { useState } from 'react';
import type { CSSProperties } from 'react';
import { Check, CircleAlert, Heart, HelpCircle, MessageCircle, MoreHorizontal, Music2, Search, Smile } from 'lucide-react';
import idle from './assets/idle.png';
import studyAtlas from './assets/lexi-study-atlas-v2.png';
import type { MascotAssets, MascotGesture, MascotState } from './mascotTypes';
import { MASCOT_ATLAS_POSES, resolveMascotArtwork } from './mascotArtwork';
import { useMascotReducedMotion } from './useMascotReducedMotion';

// One transparent sheet supplies eight static learning poses. Gestures remain
// finite CSS motions, and the original bundled sprite is the missing-file fallback.
const defaultAssets: MascotAssets = {
  idle,
  fallback: idle,
  pixelated: false,
  atlas: {
    src: studyAtlas, columns: 4, rows: 2, cellWidth: 384, cellHeight: 512, poses: MASCOT_ATLAS_POSES,
    poseAdjustments: { thinking: { clipRight: 12 }, explaining: { sourceOffsetX: -12 } },
  },
};

const gestureMoods = { nod: 'smile', tilt: 'curious', hop: 'heart', stretch: 'music', pet: 'heart' } as const;
const stateMoods = { thinking: 'thinking', searching: 'searching', explaining: 'explaining', success: 'success', uncertain: 'curious', error: 'error' } as const;
const moodIcons = { smile: Smile, curious: HelpCircle, heart: Heart, music: Music2, thinking: MoreHorizontal, searching: Search, explaining: MessageCircle, success: Check, error: CircleAlert };

export function MascotCharacter({ state, reactionId, gesture, interactionEnabled, side, pressed, dragging, assets = defaultAssets }: {
  state: MascotState; reactionId: number; gesture: MascotGesture | null; interactionEnabled: boolean;
  side: 'left' | 'right'; pressed: boolean; dragging: boolean; assets?: MascotAssets;
}) {
  const [failedSources, setFailedSources] = useState<string[]>([]);
  const reducedMotion = useMascotReducedMotion();
  const activeGesture = interactionEnabled && !dragging ? gesture : null;
  const artwork = resolveMascotArtwork({ assets, state, gesture: activeGesture, interactionEnabled, dragging,
    reducedMotion, failedSources, fallbackSource: idle });
  const stateMood = state in stateMoods ? stateMoods[state as keyof typeof stateMoods] : null;
  const mood = interactionEnabled && !dragging ? stateMood ?? (activeGesture ? gestureMoods[activeGesture.kind] : null) : null;
  const MoodIcon = mood ? moodIcons[mood] : null;
  const image = <img src={artwork.source} alt="Lexi 阅读助手" draggable={false}
    className={artwork.frame ? 'mascot-atlas-image' : undefined} style={artwork.frame?.style}
    data-pixelated={artwork.pixelated} data-asset={artwork.asset}
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
