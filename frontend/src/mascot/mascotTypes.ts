import type { MascotEvent } from './mascotEvents';
import type { AssistantReadingContext } from './mascotAssistant';

export type MascotState = 'idle' | 'reading' | 'thinking' | 'explaining' | 'searching' | 'success' | 'uncertain' | 'error';
export type MascotGestureKind = 'nod' | 'tilt' | 'hop' | 'stretch' | 'pet';
export type MascotGesture = { id: number; kind: MascotGestureKind; until: number };

export type MascotAssets = {
  idle: string;
  fallback?: string;
  pixelated?: boolean;
  thinking?: string;
  happy?: string;
  error?: string;
  pet?: string;
};

export type MascotConfig = {
  x: number | null;
  y: number | null;
  anchor: 'left' | 'right' | 'bottom' | 'free';
  side: 'left' | 'right';
  scale: number;
  hidden: boolean;
  idleBubbleEnabled: boolean;
  interactionEnabled: boolean;
};

export type MascotBubbleValue = { id: number; text: string; priority: number };

export type MascotSnapshot = {
  state: MascotState;
  reactionId: number;
  bubble: MascotBubbleValue | null;
  config: MascotConfig;
  suppressed: boolean;
  gesture: MascotGesture | null;
  interactionBlocked: boolean;
  assistantOpen: boolean;
  readingContext: AssistantReadingContext;
};

export type MascotApi = {
  emit: (event: MascotEvent) => void;
  cancelOperation: (operationId: string) => void;
  setEnvironment: (environment: { suppressed: boolean }) => void;
  updateConfig: (patch: Partial<MascotConfig>) => void;
  resetPosition: () => void;
  show: () => void;
  hide: () => void;
  interact: () => void;
  dismissBubble: () => void;
  playGesture: (kind: MascotGestureKind) => void;
  cancelGesture: () => void;
  setReadingContext: (context: AssistantReadingContext) => void;
  openAssistant: (context?: AssistantReadingContext) => void;
  closeAssistant: () => void;
};

export type { MascotEvent } from './mascotEvents';
