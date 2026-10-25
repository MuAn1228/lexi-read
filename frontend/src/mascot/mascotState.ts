import type { MascotEvent } from './mascotEvents';
import type { MascotBubbleValue, MascotConfig, MascotState } from './mascotTypes';
import { chooseMascotMessage } from './mascotMessages';
import type { MascotMessageGroup } from './mascotMessages';

export const MASCOT_STORAGE_KEY = 'lexi:mascot:config';
export const MASCOT_BUBBLE_COOLDOWN = 2500;
export const defaultMascotConfig: MascotConfig = {
  x: null, y: null, anchor: 'right', side: 'right', scale: 1, hidden: false, idleBubbleEnabled: true,
};

export function normalizeMascotConfig(value: unknown): MascotConfig {
  const candidate = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const coordinate = (input: unknown) => typeof input === 'number' && Number.isFinite(input) && Math.abs(input) <= 100000 ? input : null;
  const anchor = candidate.anchor;
  return {
    x: coordinate(candidate.x), y: coordinate(candidate.y),
    anchor: anchor === 'left' || anchor === 'bottom' || anchor === 'free' ? anchor : 'right',
    side: candidate.side === 'left' ? 'left' : 'right',
    scale: typeof candidate.scale === 'number' && Number.isFinite(candidate.scale) ? Math.max(0.75, Math.min(1.25, candidate.scale)) : 1,
    hidden: candidate.hidden === true,
    idleBubbleEnabled: typeof candidate.idleBubbleEnabled === 'boolean' ? candidate.idleBubbleEnabled : true,
  };
}

export function parseMascotConfig(value: string | null): MascotConfig {
  try { return normalizeMascotConfig(value === null ? null : JSON.parse(value)); }
  catch { return { ...defaultMascotConfig }; }
}

type PendingOperation = { id: string; kind: 'analysis' | 'recommendation'; word: string; order: number };
type TransientState = { state: MascotState; priority: number; until: number };
type TimedBubble = MascotBubbleValue & { until: number; operationId?: string };
export type MascotRuntime = {
  pending: PendingOperation[];
  transient: TransientState | null;
  bubble: TimedBubble | null;
  reactionId: number;
  sequence: number;
  lastOrdinaryBubbleAt: number;
  lastMessage: string | null;
  feedbackIds: string[];
};

type EventAction = { type: 'event'; event: MascotEvent; now: number; sample: number; choice: number };
export type MascotAction = EventAction
  | { type: 'cancel'; operationId: string; now: number }
  | { type: 'tick'; now: number }
  | { type: 'dismiss'; now: number }
  | { type: 'interact'; now: number; choice: number; enabled: boolean };

export function createMascotRuntime(): MascotRuntime {
  return { pending: [], transient: null, bubble: null, reactionId: 0, sequence: 0,
    lastOrdinaryBubbleAt: -Infinity, lastMessage: null, feedbackIds: [] };
}

export function mascotCurrentState(runtime: MascotRuntime): MascotState {
  if (runtime.transient && (runtime.transient.priority >= 2 || !runtime.pending.length)) return runtime.transient.state;
  const pending = runtime.pending.at(-1);
  return pending ? pending.kind === 'analysis' ? 'thinking' : 'searching' : 'idle';
}

export function mascotNextExpiry(runtime: MascotRuntime): number | null {
  const expires = [runtime.transient?.until, runtime.bubble?.until].filter((value): value is number => value !== undefined);
  return expires.length ? Math.min(...expires) : null;
}

function advance(runtime: MascotRuntime, now: number): MascotRuntime {
  const transient = runtime.transient && runtime.transient.until > now ? runtime.transient : null;
  const bubble = runtime.bubble && runtime.bubble.until > now ? runtime.bubble : null;
  return transient === runtime.transient && bubble === runtime.bubble ? runtime : { ...runtime, transient, bubble };
}

function react(runtime: MascotRuntime, state: MascotState, priority: number, now: number, duration: number): MascotRuntime {
  const next = { ...runtime, reactionId: runtime.reactionId + 1 };
  if ((runtime.transient?.priority ?? -1) > priority || (runtime.pending.length && priority < 2)) return next;
  return { ...next, transient: { state, priority, until: now + duration } };
}

function speak(runtime: MascotRuntime, group: MascotMessageGroup, priority: number, now: number, choice: number, count = 0, operationId?: string): MascotRuntime {
  if ((runtime.bubble?.priority ?? -1) > priority || (runtime.transient?.priority ?? -1) > priority || (runtime.pending.length && priority < 2)) return runtime;
  if (priority < 2 && now - runtime.lastOrdinaryBubbleAt < MASCOT_BUBBLE_COOLDOWN) return runtime;
  const text = chooseMascotMessage(group, runtime.lastMessage, choice, count);
  const id = runtime.sequence + 1;
  return { ...runtime, sequence: id, lastMessage: text,
    lastOrdinaryBubbleAt: priority < 2 ? now : runtime.lastOrdinaryBubbleAt,
    bubble: { id, text, priority, until: now + (priority === 3 ? 7000 : 4000), operationId } };
}

/** Pure visual reducer: no requests, storage writes, focus, DOM reads or learning events. */
export function reduceMascot(runtime: MascotRuntime, action: MascotAction): MascotRuntime {
  const current = advance(runtime, action.now);
  if (action.type === 'tick') return current;
  if (action.type === 'dismiss') return current.bubble ? { ...current, bubble: null } : current;
  if (action.type === 'cancel') {
    const pending = current.pending.filter(item => item.id !== action.operationId);
    return pending.length === current.pending.length ? current : {
      ...current, pending, bubble: current.bubble?.operationId === action.operationId ? null : current.bubble,
    };
  }
  if (action.type === 'interact') {
    const next = { ...current, reactionId: current.reactionId + 1 };
    return action.enabled ? speak(next, 'idle', 0, action.now, action.choice) : next;
  }

  const { event, now, sample, choice } = action;
  if (event.type === 'WORD_SELECTED') {
    const next = react(current, 'reading', 1, now, 1400);
    return sample < 0.15 ? speak(next, 'selected', 1, now, choice) : next;
  }
  if (event.type === 'WORD_MARKED') {
    const state = event.status === 'KNOWN' ? 'success' : event.status === 'UNKNOWN' ? 'thinking' : 'uncertain';
    const group = event.status === 'KNOWN' ? 'known' : event.status === 'UNKNOWN' ? 'unknown' : 'uncertain';
    const next = react(current, state, 1, now, 1400);
    return sample < 0.12 ? speak(next, group, 1, now, choice) : next;
  }
  if (event.type === 'FEEDBACK_SAVED') {
    if (event.requestId && current.feedbackIds.includes(event.requestId)) return current;
    const next = react({ ...current, feedbackIds: event.requestId ? [...current.feedbackIds.slice(-127), event.requestId] : current.feedbackIds }, 'success', 1, now, 1400);
    return speak(next, 'feedback', 1, now, choice);
  }

  const kind = event.type.startsWith('ANALYSIS_') ? 'analysis' : 'recommendation';
  if (event.type === 'ANALYSIS_STARTED' || event.type === 'RECOMMENDATION_STARTED') {
    if (!event.operationId || current.pending.some(item => item.id === event.operationId)) return current;
    const order = current.sequence + 1;
    const next: MascotRuntime = { ...current, sequence: order, reactionId: current.reactionId + 1,
      transient: current.transient && current.transient.priority > 2 ? current.transient : null,
      pending: [...current.pending, { id: event.operationId, kind, word: event.word, order }] };
    return speak(next, kind === 'analysis' ? 'analyzing' : 'recommending', 2, now, choice, 0, event.operationId);
  }

  if (!current.pending.some(item => item.id === event.operationId && item.kind === kind)) return current;
  const completed = { ...current, pending: current.pending.filter(item => item.id !== event.operationId) };
  if (event.type === 'ANALYSIS_ERROR' || event.type === 'RECOMMENDATION_ERROR') {
    return speak(react(completed, 'error', 3, now, 7000), 'error', 3, now, choice, 0, event.operationId);
  }
  if (event.type === 'ANALYSIS_SUCCESS') {
    const group = event.fallback === true ? 'fallback' : event.source === 'offline' ? 'analysisOffline' : 'analysisSuccess';
    return speak(react(completed, 'explaining', 2, now, 4000), group, 2, now, choice, 0, event.operationId);
  }
  const count = Number.isFinite(event.count) ? Math.max(0, Math.min(3, Math.floor(event.count))) : 0;
  const group = event.fallback === true ? 'fallback' : count === 0 ? 'recommendationEmpty' : event.source === 'offline' ? 'recommendationOffline' : 'recommendationSuccess';
  return speak(react(completed, 'success', 2, now, 4000), group, 2, now, choice, count, event.operationId);
}
