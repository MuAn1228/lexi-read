import { useState } from 'react';
import type { CSSProperties } from 'react';
import idle from './assets/idle.png';
import type { MascotAssets, MascotState } from './mascotTypes';

// The upstream portrait is deliberately local-only, outside the public source tree.
// Fresh checkouts keep a small original sprite as a self-contained fallback.
const defaultAssets: MascotAssets = { idle: `${import.meta.env.BASE_URL}local-mascot/DSniang1.png`, fallback: idle, pixelated: false };

export function MascotCharacter({ state, reactionId, side, pressed, dragging, compact, assets = defaultAssets }: {
  state: MascotState; reactionId: number; side: 'left' | 'right'; pressed: boolean; dragging: boolean; compact: boolean; assets?: MascotAssets;
}) {
  const [failedSources, setFailedSources] = useState<string[]>([]);
  const stateSource = state === 'thinking' || state === 'searching' || state === 'uncertain' ? assets.thinking
    : state === 'success' || state === 'explaining' ? assets.happy : state === 'error' ? assets.error : assets.idle;
  const requested = stateSource ?? assets.idle;
  const fallback = assets.fallback ?? idle;
  const source = failedSources.includes(requested) ? fallback : requested;
  return <span className={`mascot-character${pressed && !dragging ? ' is-pressed' : ''}${compact ? ' is-compact' : ''}`} data-testid="mascot-character" data-side={side}>
    <span className="mascot-character-facing" style={{ '--mascot-facing': side === 'left' ? -1 : 1 } as CSSProperties}>
      <span className="mascot-character-breath">
        <span key={reactionId} className={`mascot-character-reaction is-${state}`}>
          <img src={source} alt="Lexi 阅读助手" draggable={false}
            data-pixelated={source === fallback || assets.pixelated !== false}
            onError={() => {
              if (source !== fallback) setFailedSources(values => values.includes(requested) ? values : [...values, requested]);
            }} />
        </span>
      </span>
    </span>
    <span className="mascot-state-light" data-state={state} aria-hidden="true" />
  </span>;
}
