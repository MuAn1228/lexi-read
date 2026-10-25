import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const transpile = source => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const dataUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const interactionJs = transpile(await readFile(new URL('../src/mascot/mascotInteraction.ts', import.meta.url), 'utf8'));
const { createMascotInteraction, reduceMascotInteraction, MASCOT_GESTURES,
  MASCOT_DOUBLE_CLICK_MS, MASCOT_GESTURE_COOLDOWN_MS } = await import(dataUrl(interactionJs));
const messagesJs = transpile(await readFile(new URL('../src/mascot/mascotMessages.ts', import.meta.url), 'utf8'));
const stateJs = transpile(await readFile(new URL('../src/mascot/mascotState.ts', import.meta.url), 'utf8'))
  .replace("'./mascotMessages'", JSON.stringify(dataUrl(messagesJs)));
const { createMascotRuntime, reduceMascot, mascotCurrentState, mascotInteractionBlocked,
  normalizeMascotConfig } = await import(dataUrl(stateJs));

const click = (state, now, patch = {}) => reduceMascotInteraction(state,
  { type: 'click', now, choice: 0, enabled: true, blocked: false, ...patch });
const play = (state, kind, now, patch = {}) => reduceMascotInteraction(state,
  { type: 'play', kind, now, enabled: true, blocked: false, ...patch });
const tick = (state, now) => reduceMascotInteraction(state, { type: 'tick', now });
const event = (state, value, now) => reduceMascot(state,
  { type: 'event', event: value, now, sample: 1, choice: 0 });
const line = (state, kind, now, enabled = true) => reduceMascot(state,
  { type: 'gestureLine', kind, now, choice: 0, enabled });

test('gesture metadata defines five finite actions with an initially empty overlay', () => {
  assert.deepEqual(MASCOT_GESTURES.map(({ kind, duration }) => [kind, duration]),
    [['nod', 600], ['tilt', 800], ['hop', 900], ['stretch', 1100], ['pet', 1000]]);
  assert.equal(MASCOT_DOUBLE_CLICK_MS, 320);
  assert.equal(MASCOT_GESTURE_COOLDOWN_MS, 450);
  assert.equal(createMascotInteraction().gesture, null);
  assert.ok(MASCOT_GESTURES.every(item => item.label && item.description));
});

test('a single click reacts immediately and random gestures never repeat consecutively', () => {
  let state = click(createMascotInteraction(), 1000);
  assert.deepEqual(state.gesture, { id: 1, kind: 'nod', until: 1600 });
  for (let i = 1; i <= 8; i++) {
    const previous = state.gesture.kind;
    state = click(state, 1000 + i * 1500);
    assert.notEqual(state.gesture.kind, previous);
    assert.ok(['nod', 'tilt', 'hop'].includes(state.gesture.kind));
  }
});

test('one fast second click becomes pet immediately while subsequent clicks cannot extend it', () => {
  let state = click(createMascotInteraction(), 1000);
  state = click(state, 1320);
  assert.deepEqual(state.gesture, { id: 2, kind: 'pet', until: 2320 });
  assert.equal(state.lastClickAt, null);
  for (const now of [1321, 1400, 1800, 2000, 2319]) assert.equal(click(state, now), state);
  state = click(state, 2320);
  assert.equal(state.gesture.id, 3);
  assert.notEqual(state.gesture.kind, 'pet');
});

test('slow extra clicks obey cooldown without queuing a delayed reaction', () => {
  let state = click(createMascotInteraction(), 1000);
  assert.equal(click(state, 1321), state);
  assert.equal(click(state, 1449), state);
  state = click(state, 1450);
  assert.equal(state.gesture.kind, 'tilt');
  assert.equal(state.gesture.id, 2);
  state = tick(state, 2250);
  assert.equal(state.gesture, null);
  assert.equal(state.sequence, 2);
  assert.equal(tick(state, 9000), state);
});

test('manual actions share cooldown and leave only one finite overlay', () => {
  let state = play(createMascotInteraction(), 'stretch', 1000);
  assert.deepEqual(state.gesture, { id: 1, kind: 'stretch', until: 2100 });
  assert.equal(play(state, 'hop', 1449), state);
  state = play(state, 'hop', 1450);
  assert.deepEqual(state.gesture, { id: 2, kind: 'hop', until: 2350 });
  assert.equal(tick(state, 2100), state, 'an older timer must not remove the replacement');
  assert.equal(tick(state, 2350).gesture, null);
});

test('manual pet does not restart itself while active and cannot form a double-click chain', () => {
  let state = play(createMascotInteraction(), 'pet', 1000);
  assert.equal(play(state, 'pet', 1500), state);
  assert.equal(click(state, 1500), state);
  assert.equal(state.lastClickAt, null);
  state = play(state, 'pet', 2000);
  assert.equal(state.gesture.id, 2);
  assert.equal(state.gesture.until, 3000);
});

test('cancel clears overlay, click pairing and cooldown while keeping gesture identities monotonic', () => {
  let state = click(createMascotInteraction(), 1000);
  state = reduceMascotInteraction(state, { type: 'cancel' });
  assert.equal(state.gesture, null);
  assert.equal(state.lastClickAt, null);
  assert.equal(reduceMascotInteraction(state, { type: 'cancel' }), state);
  state = click(state, 1001);
  assert.equal(state.gesture.id, 2);
  assert.equal(state.gesture.kind, 'tilt');
});

test('disabled or blocked interactions cancel the overlay without scheduling another action', () => {
  for (const patch of [{ enabled: false }, { blocked: true }]) {
    const active = click(createMascotInteraction(), 1000);
    const cancelled = click(active, 1100, patch);
    assert.equal(cancelled.gesture, null);
    assert.equal(cancelled.lastClickAt, null);
    assert.equal(play(cancelled, 'hop', 1200, patch), cancelled);
    assert.equal(tick(cancelled, 8000), cancelled);
  }
});

test('pending AI and important unexpired results block gestures; ordinary word state does not', () => {
  let state = createMascotRuntime();
  assert.equal(mascotInteractionBlocked(state, 1000), false);
  state = event(state, { type: 'WORD_SELECTED', word: 'adapt' }, 1000);
  assert.equal(mascotInteractionBlocked(state, 1001), false);
  state = event(state, { type: 'ANALYSIS_STARTED', operationId: 'a', word: 'adapt' }, 1002);
  assert.equal(mascotInteractionBlocked(state, 50000), true, 'pending operations outlive their speech bubble');
  state = event(state, { type: 'ANALYSIS_SUCCESS', operationId: 'a', word: 'adapt' }, 2000);
  assert.equal(mascotInteractionBlocked(state, 5999), true);
  assert.equal(mascotInteractionBlocked(state, 6000), false);
  state = event(state, { type: 'RECOMMENDATION_STARTED', operationId: 'b', word: 'adapt' }, 6001);
  state = event(state, { type: 'RECOMMENDATION_ERROR', operationId: 'b', word: 'adapt' }, 6002);
  assert.equal(mascotInteractionBlocked(state, 13001), true);
  assert.equal(mascotInteractionBlocked(state, 13002), false);
});

test('gesture dialogue is optional, low priority, rate limited and never changes AI state or reaction IDs', () => {
  const initial = createMascotRuntime();
  assert.equal(line(initial, 'nod', 1000, false), initial);
  let state = line(initial, 'nod', 1000);
  assert.equal(state.bubble.priority, 0);
  assert.equal(state.reactionId, initial.reactionId);
  assert.equal(mascotCurrentState(state), 'idle');
  assert.equal(line(state, 'hop', 1200), state);
  state = event(state, { type: 'ANALYSIS_STARTED', operationId: 'a', word: 'adapt' }, 1300);
  const pending = state.pending;
  const bubble = state.bubble;
  assert.equal(line(state, 'pet', 1400), state);
  assert.equal(state.pending, pending);
  assert.equal(state.bubble, bubble);
  assert.equal(mascotCurrentState(state), 'thinking');
  state = event(state, { type: 'ANALYSIS_ERROR', operationId: 'a', word: 'adapt' }, 1500);
  assert.equal(line(state, 'stretch', 5000), state);
  assert.equal(mascotCurrentState(state), 'error');
});

test('old configurations enable interactions and explicit disabled preference is preserved', () => {
  assert.equal(normalizeMascotConfig({ idleBubbleEnabled: false }).interactionEnabled, true);
  assert.equal(normalizeMascotConfig({ interactionEnabled: 'false' }).interactionEnabled, true);
  assert.equal(normalizeMascotConfig({ interactionEnabled: false }).interactionEnabled, false);
});
