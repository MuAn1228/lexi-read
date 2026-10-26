import type { MascotEvent } from './mascotEvents';
import type { AssistantReadingContext } from './mascotAssistant';

export type MascotState = 'idle' | 'reading' | 'thinking' | 'explaining' | 'searching' | 'success' | 'uncertain' | 'error';
export type MascotGestureKind = 'nod' | 'tilt' | 'hop' | 'stretch' | 'pet' | 'wave' | 'clap' | 'read' | 'yawn' | 'think';
export type MascotScene = 'reader' | 'dictionary' | 'library' | 'vocabulary' | 'review' | 'practice' | 'history' | 'dashboard' | 'relations' | 'data';
export type MascotGesture = { id: number; kind: MascotGestureKind; until: number };

export type MascotAtlas = {
  src: string;
  columns: number;
  rows: number;
  cellWidth: number;
  cellHeight: number;
  poses: Record<MascotState, number>;
  /** Corrections measured in original sheet pixels, without resizing a pose. */
  poseAdjustments?: Partial<Record<MascotState, { sourceOffsetX?: number; clipRight?: number }>>;
};

export type MascotActionAtlas = {
  src: string;
  columns: number;
  rows: number;
  cellWidth: number;
  cellHeight: number;
  actions: Partial<Record<MascotGestureKind, { frames: readonly number[]; frameDuration: number; duration: number }>>;
  frameAdjustments?: Partial<Record<number, { sourceOffsetX?: number; clipRight?: number }>>;
};

export type MascotAssets = {
  idle: string;
  fallback?: string;
  pixelated?: boolean;
  thinking?: string;
  happy?: string;
  error?: string;
  pet?: string;
  atlas?: MascotAtlas;
  actions?: MascotActionAtlas;
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
  setEnvironment: (environment: { suppressed: boolean; scene?: MascotScene }) => void;
  updateConfig: (patch: Partial<MascotConfig>) => void;
  resetPosition: () => void;
  show: () => void;
  hide: () => void;
  interact: () => void;
  speak: () => void;
  dismissBubble: () => void;
  playGesture: (kind: MascotGestureKind) => void;
  cancelGesture: () => void;
  setReadingContext: (context: AssistantReadingContext) => void;
  openAssistant: (context?: AssistantReadingContext) => void;
  closeAssistant: () => void;
};

export type { MascotEvent } from './mascotEvents';
