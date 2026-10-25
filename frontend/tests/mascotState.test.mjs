import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const transpile = source => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const dataUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const messagesJs = transpile(await readFile(new URL('../src/mascot/mascotMessages.ts', import.meta.url), 'utf8'));
const stateJs = transpile(await readFile(new URL('../src/mascot/mascotState.ts', import.meta.url), 'utf8'))
  .replace("'./mascotMessages'", JSON.stringify(dataUrl(messagesJs)));
const { createMascotRuntime, reduceMascot, mascotCurrentState, mascotNextExpiry, parseMascotConfig,
  normalizeMascotConfig, defaultMascotConfig, MASCOT_STORAGE_KEY } = await import(dataUrl(stateJs));
const { chooseMascotMessage, mascotMessages } = await import(dataUrl(messagesJs));

const event = (runtime, value, now = 10000, sample = 0, choice = 0) => reduceMascot(runtime,
  { type: 'event', event: value, now, sample, choice });
const start = (id = 'a', type = 'ANALYSIS_STARTED') => ({ type, operationId: id, word: 'adapt' });
const success = (id = 'a', patch = {}) => ({ type: 'ANALYSIS_SUCCESS', operationId: id, word: 'adapt', ...patch });
const tick = (runtime, now) => reduceMascot(runtime, { type: 'tick', now });

test('config validates persisted primitives and safely recovers corrupt or missing storage', () => {
  assert.equal(MASCOT_STORAGE_KEY, 'lexi:mascot:config');
  for (const input of [null, '{bad', 'null', '[]', '42']) assert.deepEqual(parseMascotConfig(input), defaultMascotConfig);
  assert.deepEqual(normalizeMascotConfig({ x: Infinity, y: '30', anchor: 'top', side: 'bad', scale: -10, hidden: 'true' }),
    { ...defaultMascotConfig, scale: .75 });
  const valid = { x: 24, y: 40, anchor: 'left', side: 'left', scale: 1.25, hidden: true, idleBubbleEnabled: false };
  assert.deepEqual(parseMascotConfig(JSON.stringify(valid)), { ...valid, interactionEnabled: true });
  assert.equal(parseMascotConfig(JSON.stringify({ ...valid, interactionEnabled: false })).interactionEnabled, false);
  assert.equal(normalizeMascotConfig({ scale: 20 }).scale, 1.25);
  assert.equal(normalizeMascotConfig({ scale: NaN }).scale, 1);
});

test('ordinary events always react but only sampled bubbles pass a 2500ms cooldown', () => {
  let state = event(createMascotRuntime(), { type: 'WORD_SELECTED', word: 'one' });
  const first = state.bubble;
  assert.equal(mascotCurrentState(state), 'reading');
  state = event(state, { type: 'WORD_MARKED', word: 'one', status: 'UNCERTAIN' }, 10001);
  assert.equal(mascotCurrentState(state), 'uncertain');
  assert.equal(state.reactionId, 2);
  assert.equal(state.bubble, first);
  state = event(state, { type: 'WORD_SELECTED', word: 'two' }, 12500, .9);
  assert.equal(state.reactionId, 3);
  assert.equal(state.bubble, first);
  state = event(state, { type: 'WORD_SELECTED', word: 'two' }, 12501, .1499);
  assert.notEqual(state.bubble.id, first.id);
  state = tick(state, 16501);
  assert.equal(state.bubble, null);
  assert.equal(mascotCurrentState(state), 'idle');
});

test('known, unknown and uncertain confirmed markings map to distinct visual states', () => {
  for (const [status, expected] of [['KNOWN', 'success'], ['UNKNOWN', 'thinking'], ['UNCERTAIN', 'uncertain']]) {
    const state = event(createMascotRuntime(), { type: 'WORD_MARKED', word: 'adapt', status }, 10000, 1);
    assert.equal(mascotCurrentState(state), expected);
    assert.equal(state.bubble, null);
    assert.equal(mascotCurrentState(tick(state, 11400)), 'idle');
  }
});

test('an AI start bypasses normal cooldown, remains active after loading bubble expires', () => {
  let state = event(createMascotRuntime(), { type: 'WORD_SELECTED', word: 'adapt' });
  state = event(state, start(), 10001);
  assert.equal(mascotCurrentState(state), 'thinking');
  assert.equal(state.bubble.priority, 2);
  state = tick(state, 14001);
  assert.equal(state.bubble, null);
  assert.equal(mascotCurrentState(state), 'thinking');
  state = event(state, { type: 'WORD_MARKED', word: 'other', status: 'KNOWN' }, 15000);
  assert.equal(mascotCurrentState(state), 'thinking');
  assert.equal(state.bubble, null);
});

test('concurrent operations restore the latest remaining request after cancel or result expiry', () => {
  let state = event(createMascotRuntime(), start('a'));
  state = event(state, start('b', 'RECOMMENDATION_STARTED'), 10001);
  assert.equal(mascotCurrentState(state), 'searching');
  state = reduceMascot(state, { type: 'cancel', operationId: 'b', now: 10002 });
  assert.equal(mascotCurrentState(state), 'thinking');
  assert.equal(state.bubble, null);
  state = event(state, start('c', 'RECOMMENDATION_STARTED'), 10003);
  state = event(state, success('a'), 10004);
  assert.equal(mascotCurrentState(state), 'explaining');
  state = tick(state, 14004);
  assert.equal(mascotCurrentState(state), 'searching');
  state = reduceMascot(state, { type: 'cancel', operationId: 'c', now: 14005 });
  assert.equal(mascotCurrentState(state), 'idle');
});

test('unknown, stale, duplicate and mismatched operation results do not change state', () => {
  let state = event(createMascotRuntime(), start('a'));
  assert.equal(event(state, start('a'), 10001), state);
  assert.equal(event(state, success('missing'), 10001), state);
  assert.equal(event(state, { type: 'RECOMMENDATION_SUCCESS', operationId: 'a', word: 'adapt', count: 2 }, 10001), state);
  state = reduceMascot(state, { type: 'cancel', operationId: 'a', now: 10001 });
  assert.equal(event(state, success('a'), 10002), state);
  assert.equal(event(state, { type: 'ANALYSIS_ERROR', operationId: 'a', word: 'adapt' }, 10002), state);
});

test('errors outrank new AI starts, results, ordinary events and idle messages until expiry', () => {
  let state = event(createMascotRuntime(), start('a'));
  state = event(state, { type: 'ANALYSIS_ERROR', operationId: 'a', word: 'adapt' }, 10001);
  const errorBubble = state.bubble;
  state = event(state, start('b'), 10002);
  state = event(state, { type: 'WORD_SELECTED', word: 'other' }, 10003);
  state = reduceMascot(state, { type: 'interact', now: 10004, choice: 0, enabled: true });
  state = event(state, success('b'), 10005);
  assert.equal(mascotCurrentState(state), 'error');
  assert.equal(state.bubble, errorBubble);
  state = tick(state, 17001);
  assert.equal(mascotCurrentState(state), 'idle');
  assert.equal(state.bubble, null);
});

test('offline results use neutral messages; fallback is named only with explicit evidence', () => {
  let state = event(createMascotRuntime(), start());
  state = event(state, success('a', { source: 'offline' }), 10001);
  assert.match(state.bubble.text, /离线释义/);
  assert.doesNotMatch(state.bubble.text, /模型暂时没回应/);
  let fallback = event(createMascotRuntime(), start());
  fallback = event(fallback, success('a', { source: 'offline', fallback: true }), 10001);
  assert.match(fallback.bubble.text, /模型暂时没回应/);
});

test('recommendation result counts are bounded and empty results never claim a discovery', () => {
  for (const count of [0, -1, NaN]) {
    let state = event(createMascotRuntime(), start('a', 'RECOMMENDATION_STARTED'));
    state = event(state, { type: 'RECOMMENDATION_SUCCESS', operationId: 'a', word: 'adapt', count }, 10001);
    assert.match(state.bubble.text, /没有特别合适/);
  }
  let state = event(createMascotRuntime(), start('a', 'RECOMMENDATION_STARTED'));
  state = event(state, { type: 'RECOMMENDATION_SUCCESS', operationId: 'a', word: 'adapt', count: 200 }, 10001);
  assert.match(state.bubble.text, /3 个关联词/);
});

test('confirmed feedback is deduplicated using the existing request identity', () => {
  const state = event(createMascotRuntime(), { type: 'FEEDBACK_SAVED', requestId: 'existing-id' });
  assert.equal(mascotCurrentState(state), 'success');
  assert.equal(event(state, { type: 'FEEDBACK_SAVED', requestId: 'existing-id' }, 10001), state);
  assert.equal(state.feedbackIds.length, 1);
});

test('idle dialogue is explicit, optional, never repeats consecutively and expires without a new event', () => {
  const initial = createMascotRuntime();
  assert.equal(initial.bubble, null);
  const disabled = reduceMascot(initial, { type: 'interact', now: 10000, choice: 0, enabled: false });
  assert.equal(disabled.bubble, null);
  let state = reduceMascot(initial, { type: 'interact', now: 10000, choice: 0, enabled: true });
  const first = state.bubble.text;
  state = tick(state, 14000);
  assert.equal(state.bubble, null);
  state = reduceMascot(state, { type: 'interact', now: 15000, choice: 0, enabled: true });
  assert.notEqual(state.bubble.text, first);
  assert.notEqual(chooseMascotMessage('idle', first, 0), first);
});

test('expiry and dismiss do not clear a newer result and leave pending requests running', () => {
  let state = event(createMascotRuntime(), start());
  const loadingExpiry = mascotNextExpiry(state);
  state = event(state, success(), 12000);
  state = tick(state, loadingExpiry);
  assert.equal(state.bubble.priority, 2);
  assert.equal(mascotCurrentState(state), 'explaining');
  state = reduceMascot(state, { type: 'dismiss', now: 14001 });
  assert.equal(state.bubble, null);
  assert.equal(mascotCurrentState(state), 'explaining');
  assert.equal(mascotNextExpiry(state), 16000);
});

test('explicit clicks choose the current page context without starting a learning operation', () => {
  for (const scene of ['reader', 'library', 'vocabulary', 'review', 'practice', 'history', 'dashboard', 'relations', 'data']) {
    const initial = createMascotRuntime();
    const state = reduceMascot(initial, { type: 'interact', scene, now: 1000, choice: 0, enabled: true });
    assert.ok(mascotMessages[scene].includes(state.bubble.text));
    assert.equal(state.bubble.priority, 0);
    assert.equal(mascotCurrentState(state), 'idle');
    assert.equal(state.pending, initial.pending);
    assert.equal(state.feedbackIds, initial.feedbackIds);
    assert.equal(initial.bubble, null);
  }
});

test('requesting a line is separate from a gesture or ordinary click reaction', () => {
  const initial = createMascotRuntime();
  const state = reduceMascot(initial, { type: 'requestLine', scene: 'review', now: 1000, choice: 0 });
  assert.ok(mascotMessages.review.includes(state.bubble.text));
  assert.equal(state.reactionId, initial.reactionId);
  assert.equal(state.transient, initial.transient);
  assert.equal(state.pending, initial.pending);
  assert.equal(state.feedbackIds, initial.feedbackIds);
  assert.equal(state.lastRequestedBubbleAt, 1000);
  assert.equal(mascotCurrentState(state), 'idle');
});

test('deliberate lines have a short independent cooldown while ordinary speech keeps 2500 ms', () => {
  let state = reduceMascot(createMascotRuntime(), { type: 'interact', now: 1000, choice: 0, enabled: true });
  const ordinary = state.bubble;
  state = reduceMascot(state, { type: 'requestLine', scene: 'vocabulary', now: 1001, choice: 0 });
  assert.notEqual(state.bubble, ordinary);
  assert.equal(reduceMascot(state, { type: 'requestLine', now: 2200, choice: 0 }), state);
  state = reduceMascot(state, { type: 'requestLine', scene: 'vocabulary', now: 2201, choice: 0 });
  state = reduceMascot(state, { type: 'dismiss', now: 2202 });
  state = reduceMascot(state, { type: 'interact', now: 4700, choice: 0, enabled: true });
  assert.equal(state.bubble, null);
  state = reduceMascot(state, { type: 'interact', now: 4701, choice: 0, enabled: true });
  assert.ok(state.bubble);
});

test('explicit lines may replace ordinary word chatter without modifying its visual state', () => {
  const ordinary = event(createMascotRuntime(), { type: 'WORD_SELECTED', word: 'adapt' }, 1000);
  const next = reduceMascot(ordinary, { type: 'requestLine', scene: 'reader', now: 1001, choice: 0 });
  assert.notEqual(next.bubble, ordinary.bubble);
  assert.equal(next.transient, ordinary.transient);
  assert.equal(next.reactionId, ordinary.reactionId);
  assert.equal(mascotCurrentState(next), 'reading');
});

test('pending AI, confirmed AI results and errors keep priority over deliberate chatter', () => {
  let state = event(createMascotRuntime(), start(), 1000);
  assert.equal(reduceMascot(state, { type: 'requestLine', scene: 'reader', now: 1001, choice: 0 }), state);
  assert.equal(state.lastRequestedBubbleAt, -Infinity, 'blocked speech must not consume a later chance');
  state = event(state, success(), 2000);
  assert.equal(reduceMascot(state, { type: 'requestLine', now: 2001, choice: 0 }), state);
  let failed = event(createMascotRuntime(), start(), 1000);
  failed = event(failed, { type: 'ANALYSIS_ERROR', operationId: 'a', word: 'adapt' }, 2000);
  assert.equal(reduceMascot(failed, { type: 'requestLine', now: 8999, choice: 0 }), failed);
  const recovered = reduceMascot(failed, { type: 'requestLine', scene: 'reader', now: 9000, choice: 0 });
  assert.equal(mascotCurrentState(recovered), 'idle');
  assert.equal(recovered.bubble.priority, 1);
  assert.equal(recovered.lastRequestedBubbleAt, 9000);
});

test('per-group dialogue remembers recent lines across scene changes without mutating earlier runtimes', () => {
  let state = createMascotRuntime();
  const readerLines = [];
  for (let index = 0; index < 4; index++) {
    const before = state;
    const priorRecent = JSON.stringify(before.recentMessages);
    state = reduceMascot(state, { type: 'requestLine', scene: 'reader', now: index * 6000, choice: 0 });
    readerLines.push(state.bubble.text);
    assert.equal(JSON.stringify(before.recentMessages), priorRecent);
    state = reduceMascot(state, { type: 'requestLine', scene: 'library', now: index * 6000 + 3000, choice: 0 });
  }
  assert.equal(new Set(readerLines).size, 4);
  assert.equal(state.recentMessages.reader.length, 3);
  assert.ok(state.recentMessages.library.length <= 3);
});

test('the ten manual gesture groups all provide dialogue without changing learning state', () => {
  const groups = { nod: 'gestureNod', tilt: 'gestureTilt', hop: 'gestureHop', stretch: 'gestureStretch', pet: 'gesturePet',
    wave: 'gestureWave', clap: 'gestureClap', read: 'gestureRead', yawn: 'gestureYawn', think: 'gestureThink' };
  for (const [kind, group] of Object.entries(groups)) {
    const initial = createMascotRuntime();
    const state = reduceMascot(initial, { type: 'gestureLine', kind, scene: 'reader', now: 1000, choice: 0, enabled: true });
    assert.ok(mascotMessages[group].includes(state.bubble.text));
    assert.equal(state.reactionId, initial.reactionId);
    assert.equal(state.transient, null);
    assert.equal(state.pending, initial.pending);
    assert.equal(state.feedbackIds, initial.feedbackIds);
  }
});

test('time alone never creates chatter, including after an explicit line expires', () => {
  const initial = createMascotRuntime();
  assert.equal(tick(initial, 1000000), initial);
  const requested = reduceMascot(initial, { type: 'requestLine', scene: 'practice', now: 1000, choice: 0 });
  const expired = tick(requested, 5000);
  assert.equal(expired.bubble, null);
  assert.equal(tick(expired, 1000000), expired);
});
