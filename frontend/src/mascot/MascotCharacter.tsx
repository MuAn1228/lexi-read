import { useState } from 'react';
import type { CSSProperties } from 'react';
import { Check, CircleAlert, Heart, HelpCircle, MessageCircle, MoreHorizontal, Music2, Search, Smile } from 'lucide-react';
import idle from './assets/idle.png';
import type { MascotAssets, MascotGesture, MascotState } from './mascotTypes';
import { useMascotReducedMotion } from './useMascotReducedMotion';

// The upstream portrait is deliberately local-only, outside the public source tree.
// Fresh checkouts keep a small original sprite as a self-contained fallback.
const defaultAssets: MascotAssets = {
  idle: `${import.meta.env.BASE_URL}local-mascot/DSniang1.png`,
  pet: `${import.meta.env.BASE_URL}local-mascot/rua.gif`,
  fallback: idle,
  pixelated: false,
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
  const stateSource = state === 'thinking' || state === 'searching' || state === 'uncertain' ? assets.thinking
    : state === 'success' || state === 'explaining' ? assets.happy : state === 'error' ? assets.error : assets.idle;
  const fallback = assets.fallback ?? idle;
  // A failed optional GIF is remembered across reactions, then the portrait and bundled sprite remain available.
  // Reduced motion never places the GIF in an image src, so it never starts loading.
  const petSource = activeGesture?.kind === 'pet' && !reducedMotion ? assets.pet : undefined;
  const candidates = [petSource, stateSource, assets.idle, fallback, idle];
  const source = candidates.find((candidate): candidate is string => Boolean(candidate) && !failedSources.includes(candidate!)) ?? idle;
  const stateMood = state in stateMoods ? stateMoods[state as keyof typeof stateMoods] : null;
  const mood = interactionEnabled && !dragging ? stateMood ?? (activeGesture ? gestureMoods[activeGesture.kind] : null) : null;
  const MoodIcon = mood ? moodIcons[mood] : null;
  return <span className={`mascot-character${pressed && !dragging ? ' is-pressed' : ''}`} data-testid="mascot-character" data-side={side}>
    <span key={activeGesture?.id ?? 'rest'} className="mascot-character-gesture" data-testid="mascot-gesture-layer" data-gesture={activeGesture?.kind ?? 'none'}>
      <span className="mascot-character-facing" style={{ '--mascot-facing': side === 'left' ? -1 : 1 } as CSSProperties}>
        <span className="mascot-character-breath">
          <span key={reactionId} className={`mascot-character-reaction is-${state}`}>
            <img src={source} alt="Lexi 阅读助手" draggable={false}
              data-pixelated={source === fallback || source === idle || assets.pixelated !== false}
              data-asset={source === petSource ? 'pet' : source === idle ? 'fallback' : 'portrait'}
              onError={() => {
                if (source !== idle) setFailedSources(values => values.includes(source) ? values : [...values, source]);
              }} />
          </span>
        </span>
      </span>
    </span>
    {MoodIcon && <span className="mascot-emotion-badge" data-testid="mascot-emotion-badge" data-mood={mood} aria-hidden="true"><MoodIcon size={13} strokeWidth={1.8} /></span>}
    <span className="mascot-state-light" data-state={state} aria-hidden="true" />
  </span>;
}
