import type { MascotGesture, MascotGestureKind } from './mascotTypes';

export const MASCOT_GESTURES: readonly { kind: MascotGestureKind; label: string; description: string; duration: number }[] = [
  { kind: 'nod', label: '点点头', description: '轻轻点头回应你', duration: 600 },
  { kind: 'tilt', label: '歪歪头', description: '好奇地歪一下头', duration: 800 },
  { kind: 'hop', label: '轻轻跳', description: '原地轻跳一次', duration: 900 },
  { kind: 'stretch', label: '伸个懒腰', description: '安静地伸展一下', duration: 1100 },
  { kind: 'pet', label: '摸摸头', description: '轻轻摸摸头', duration: 1000 },
  { kind: 'wave', label: '挥挥手', description: '向你挥手打个招呼', duration: 1600 },
  { kind: 'clap', label: '拍拍手', description: '为你的努力鼓掌', duration: 1600 },
  { kind: 'read', label: '翻翻书', description: '一起读上一小段', duration: 2200 },
  { kind: 'yawn', label: '打个哈欠', description: '短暂放松一下', duration: 2000 },
  { kind: 'think', label: '认真思考', description: '停下来想一想', duration: 1600 },
];
export const MASCOT_DOUBLE_CLICK_MS = 320;
export const MASCOT_GESTURE_COOLDOWN_MS = 450;
const randomGestures: readonly MascotGestureKind[] = ['nod', 'tilt', 'hop', 'wave', 'clap', 'think'];

export type MascotInteractionRuntime = {
  gesture: MascotGesture | null;
  sequence: number;
  lastRandomKind: MascotGestureKind | null;
  lastClickAt: number | null;
  lastGestureAt: number;
};
export type MascotInteractionAction =
  | { type: 'click'; now: number; choice: number; enabled: boolean; blocked: boolean }
  | { type: 'play'; now: number; kind: MascotGestureKind; enabled: boolean; blocked: boolean }
  | { type: 'tick'; now: number }
  | { type: 'cancel' };

export function createMascotInteraction(): MascotInteractionRuntime {
  return { gesture: null, sequence: 0, lastRandomKind: null, lastClickAt: null, lastGestureAt: -Infinity };
}

function cancel(runtime: MascotInteractionRuntime): MascotInteractionRuntime {
  return runtime.gesture === null && runtime.lastClickAt === null && runtime.lastGestureAt === -Infinity ? runtime
    : { ...runtime, gesture: null, lastClickAt: null, lastGestureAt: -Infinity };
}

function play(runtime: MascotInteractionRuntime, kind: MascotGestureKind, now: number): MascotInteractionRuntime {
  const duration = MASCOT_GESTURES.find(gesture => gesture.kind === kind)?.duration;
  if (duration === undefined) return runtime;
  const id = runtime.sequence + 1;
  return { ...runtime, sequence: id, gesture: { id, kind, until: now + duration }, lastGestureAt: now };
}

/** Only one finite visual gesture at a time; no timers, DOM, audio, learning actions or queue. */
export function reduceMascotInteraction(runtime: MascotInteractionRuntime, action: MascotInteractionAction): MascotInteractionRuntime {
  if (action.type === 'cancel') return cancel(runtime);
  const current = runtime.gesture && runtime.gesture.until <= action.now ? { ...runtime, gesture: null } : runtime;
  if (action.type === 'tick') return current;
  if (!action.enabled || action.blocked) return cancel(current);

  if (action.type === 'click') {
    const doubleClick = current.lastClickAt !== null && action.now >= current.lastClickAt
      && action.now - current.lastClickAt <= MASCOT_DOUBLE_CLICK_MS;
    // The second click may replace the first gesture immediately, once only.
    if (doubleClick && current.gesture?.kind !== 'pet') {
      return { ...play(current, 'pet', action.now), lastClickAt: null };
    }
    if (current.gesture?.kind === 'pet') return current;
    if (action.now - current.lastGestureAt < MASCOT_GESTURE_COOLDOWN_MS) return current;
    const choices = randomGestures.filter(kind => kind !== current.lastRandomKind);
    const safeChoice = Number.isFinite(action.choice) ? Math.max(0, Math.min(.999999, action.choice)) : 0;
    const kind = choices[Math.floor(safeChoice * choices.length)];
    return { ...play(current, kind, action.now), lastRandomKind: kind, lastClickAt: action.now };
  }

  if (action.now - current.lastGestureAt < MASCOT_GESTURE_COOLDOWN_MS
    || (action.kind === 'pet' && current.gesture?.kind === 'pet')) return current;
  const next = play(current, action.kind, action.now);
  if (next === current) return current;
  return { ...next, lastClickAt: null,
    lastRandomKind: randomGestures.includes(action.kind) ? action.kind : current.lastRandomKind };
}
