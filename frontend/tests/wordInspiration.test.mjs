import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

let source = await readFile(new URL('../src/wordInspiration.ts', import.meta.url), 'utf8');
for (const name of ['inspirationEveryday', 'inspirationReading']) {
  const data = await readFile(new URL(`../src/data/${name}.ts`, import.meta.url), 'utf8');
  const dataJs = ts.transpileModule(data, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  source = source.replace(`'./data/${name}'`, JSON.stringify(`data:text/javascript;base64,${Buffer.from(dataJs).toString('base64')}`));
}
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { curatedCards, personalInspirationCards, pickInspirationBatch, parseInspirationHistory, nextInspirationDraw, createInspirationExplorer } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const word = (id, lemma, status, overrides = {}) => ({
  id, user_id: 1, word: lemma, lemma, status, mastery_score: null, seen_count: 1, lookup_count: 0,
  created_at: '2026-10-20T09:00:00Z', updated_at: '2026-10-20T09:00:00Z',
  first_seen_at: null, last_seen_at: null, ...overrides,
});
const document = (words, overrides = {}) => ({ id: 7, title: 'An actual reading', deleted_at: null, words, ...overrides });
const occurrence = (id, lemma, sentence, overrides = {}) => ({ id, word: lemma, lemma, sentence, ...overrides });
const ids = cards => cards.map(card => card.id);
function seededRandom(seed) {
  let state = seed >>> 0;
  return () => { state = (state * 1664525 + 1013904223) >>> 0; return state / 2 ** 32; };
}

test('offline selection contains over four hundred distinct complete editorial cards', () => {
  assert.ok(curatedCards.length >= 400);
  assert.equal(new Set(ids(curatedCards)).size, curatedCards.length);
  assert.equal(new Set(curatedCards.map(card => card.word)).size, curatedCards.length);
  for (const card of curatedCards) {
    assert.match(card.id, /^curated:/);
    assert.match(card.word, /^[a-z]+$/);
    assert.ok(['noun', 'verb', 'adjective', 'adverb'].includes(card.kind));
    assert.match(card.meaning, /[\u4e00-\u9fff]/);
    assert.ok(card.sentence.split(/\s+/).length >= 5 && /[.!?]$/.test(card.sentence));
    assert.equal(card.sourceLabel, '离线精选');
    assert.equal(card.contextLabel, '原创示例句');
  }
  assert.ok(Object.isFrozen(curatedCards));
});

test('personal cards use only actual unfamiliar vocabulary and never borrow a generic meaning', () => {
  const vocabulary = [word(1, 'curiosity', 'KNOWN'), word(2, 'bank', 'UNKNOWN'), word(3, 'wander', 'UNCERTAIN'), word(4, 'unmarked', null)];
  const original = structuredClone(vocabulary);
  const result = personalInspirationCards(vocabulary, null);
  assert.deepEqual(result.map(card => [card.id, card.word, card.kind]), [
    ['personal:bank', 'bank', '不认识'], ['personal:wander', 'wander', '不确定'],
  ]);
  assert.ok(result.every(card => card.sourceLabel === '我的词库' && card.meaning === null && card.sentence === null));
  assert.ok(result.every(card => card.contextLabel === '当前文章中暂无原句'));
  assert.deepEqual(vocabulary, original);
});

test('personal cards retain real mastered and unmarked statuses when there are no unfamiliar words', () => {
  const result = personalInspirationCards([word(1, 'curiosity', 'KNOWN'), word(2, 'pause', null)], null);
  assert.deepEqual(result.map(card => [card.word, card.kind, card.meaning]), [
    ['curiosity', '已掌握', null], ['pause', '未标记', null],
  ]);
  assert.deepEqual(personalInspirationCards([], null), []);
});

test('original sentences match normalized lemmas, not similar spelling or unrelated document words', () => {
  const reading = document([
    occurrence(1, 'riverbank', 'The riverbank was quiet.'),
    occurrence(2, 'bank', '   '),
    occurrence(3, 'BANK', 'She sat on the bank and watched the river.', { word: 'banks' }),
    occurrence(4, 'bank', 'This later sentence is also real.'),
    occurrence(5, 'wander', 'We wandered home after dinner.', { word: 'wandered' }),
    occurrence(6, 'blue', 'The sky was blue.', { word: 'bloom' }),
  ], { title: '  Along the river  ' });
  const snapshot = structuredClone(reading);
  const result = personalInspirationCards([
    word(1, ' bank ', 'UNKNOWN', { word: ' Bank ' }), word(2, 'wander', 'UNCERTAIN'), word(3, 'bloom', 'UNKNOWN'),
  ], reading);
  assert.equal(result[0].word, 'Bank');
  assert.equal(result[0].sentence, reading.words[2].sentence);
  assert.equal(result[0].contextLabel, '原句 · Along the river');
  assert.equal(result[1].sentence, reading.words[4].sentence);
  assert.equal(result[2].sentence, null);
  assert.equal(result[2].meaning, null);
  assert.deepEqual(reading, snapshot);
});

test('no deleted article or invented sentence is used as personal context', () => {
  const vocabulary = [word(1, 'spark', 'UNKNOWN')];
  const reading = document([occurrence(1, 'spark', 'The speech sparked a debate.')]);
  assert.equal(personalInspirationCards(vocabulary, { ...reading, deleted_at: '2026-10-21' })[0].sentence, null);
  assert.equal(personalInspirationCards(vocabulary, document([]))[0].sentence, null);
  assert.equal(personalInspirationCards(vocabulary, document([occurrence(1, 'spark', '\n\t')]))[0].sentence, null);
  assert.equal(personalInspirationCards(vocabulary, { ...reading, title: '' })[0].contextLabel, '当前文章原句');
});

test('duplicate lemmas keep the most recently saved real status and stable identity', () => {
  const vocabulary = [
    word(1, 'WANDER', 'UNKNOWN', { word: 'Wander' }),
    word(2, ' wander ', 'KNOWN', { word: 'wandered', updated_at: '2026-10-21T10:00:00Z' }),
    word(3, '', 'KNOWN', { word: ' PAUSE ' }),
    word(4, 'pause', 'KNOWN'),
    word(5, 'invalid', 'UNKNOWN', { word: ' ' }),
  ];
  const result = personalInspirationCards(vocabulary, null);
  assert.deepEqual(result.map(card => [card.id, card.word, card.kind]), [
    ['personal:wander', 'wandered', '已掌握'], ['personal:pause', 'PAUSE', '已掌握'],
  ]);
  const unchanged = personalInspirationCards([vocabulary[1]], null)[0];
  assert.equal(unchanged.id, result[0].id);
  assert.equal(unchanged.accent, result[0].accent);
  assert.equal(unchanged.glow, result[0].glow);
});

test('successive random groups do not repeat the immediately preceding six when enough words exist', () => {
  const random = seededRandom(20261021);
  const snapshot = JSON.stringify(curatedCards);
  let previous = [];
  const seen = new Set();
  for (let group = 0; group < 80; group++) {
    const batch = pickInspirationBatch(curatedCards, previous, 6, random);
    assert.equal(batch.length, 6);
    assert.equal(new Set(ids(batch)).size, 6);
    assert.ok(batch.every(card => !previous.includes(card.id)));
    batch.forEach(card => seen.add(card.id));
    previous = ids(batch);
  }
  assert.ok(seen.size > 30);
  assert.equal(JSON.stringify(curatedCards), snapshot);
  const pool = curatedCards.slice(0, 12);
  const first = pickInspirationBatch(pool, [], 6, () => 0);
  const next = pickInspirationBatch(pool, ids(first), 6, () => 0);
  assert.equal(next.filter(card => ids(first).includes(card.id)).length, 0);
});

test('small pools never invent filler cards and still change their order when possible', () => {
  for (let size = 2; size <= 6; size++) {
    const pool = curatedCards.slice(0, size);
    const previous = ids(pool);
    const unchangedPrevious = [...previous];
    const next = pickInspirationBatch(pool, previous, 6, () => 1);
    assert.equal(next.length, size);
    assert.deepEqual([...ids(next)].sort(), [...previous].sort());
    assert.notDeepEqual(ids(next), previous);
    assert.deepEqual(previous, unchangedPrevious);
  }
  const singleton = curatedCards.slice(0, 1);
  assert.deepEqual(pickInspirationBatch(singleton, ids(singleton)), singleton);
  assert.deepEqual(pickInspirationBatch([], []), []);
});

test('medium pools prefer every unseen card before repeating only the necessary remainder', () => {
  const pool = curatedCards.slice(0, 8);
  const previous = ids(pool.slice(0, 6));
  const result = pickInspirationBatch(pool, previous, 6, () => 0.5);
  assert.equal(result.length, 6);
  assert.deepEqual([...ids(result.slice(0, 2))].sort(), [...ids(pool.slice(6))].sort());
  assert.equal(result.filter(card => previous.includes(card.id)).length, 4);
  assert.equal(new Set(ids(result)).size, 6);
});

test('batch picking tolerates malformed input, duplicate ids and invalid random draws without mutating inputs', () => {
  for (const limit of [0, -1, NaN, Infinity, '6', null]) assert.deepEqual(pickInspirationBatch(curatedCards, [], limit), []);
  assert.deepEqual(pickInspirationBatch(null), []);
  assert.equal(pickInspirationBatch(curatedCards, [], 2.9).length, 2);
  const duplicate = { ...curatedCards[0], word: 'Must not replace the first card' };
  const malformed = [null, {}, { ...curatedCards[1], id: ' ' }, { ...curatedCards[1], word: '' }];
  const pool = Object.freeze([curatedCards[0], duplicate, curatedCards[1], ...malformed]);
  for (const draw of [NaN, Infinity, -10, 20, '0.5', null]) {
    const batch = pickInspirationBatch(pool, null, 6, () => draw);
    assert.equal(batch.length, 2);
    assert.equal(batch.find(card => card.id === curatedCards[0].id), curatedCards[0]);
    assert.equal(new Set(ids(batch)).size, 2);
  }
  assert.equal(pickInspirationBatch(pool, [], 6, () => { throw new Error('bad random source'); }).length, 2);
  assert.deepEqual(personalInspirationCards(null, null), []);
});

const blankHistory = () => ({ version: 1, seenIds: [], lastBatchIds: [] });
function memoryStorage() {
  const values = new Map();
  let writes = 0;
  return { values, get writes() { return writes; }, getItem: key => values.get(key) ?? null,
    setItem(key, value) { writes++; values.set(key, value); } };
}

test('a whole curated round contains every real word exactly once before starting another round', () => {
  let history = blankHistory();
  const random = seededRandom(8);
  const all = [];
  let last;
  while (all.length < curatedCards.length) {
    const before = structuredClone(history);
    const draw = nextInspirationDraw(curatedCards, history, random);
    assert.ok(draw.cards.length > 0 && draw.cards.length <= 6);
    assert.ok(draw.cards.every(card => !all.includes(card.id)));
    assert.deepEqual(history, before);
    all.push(...ids(draw.cards)); history = draw.history; last = draw;
  }
  assert.equal(new Set(all).size, curatedCards.length);
  assert.equal(last.cards.length, curatedCards.length % 6 || 6);
  const restarted = nextInspirationDraw(curatedCards, history, random);
  assert.equal(restarted.cards.length, 6);
  assert.ok(restarted.cards.every(card => !ids(last.cards).includes(card.id)));
  assert.equal(restarted.history.seenIds.length, 6);
});

test('pool upgrades retain seen words and prioritize newly available content', () => {
  const old = curatedCards.slice(0, 42);
  const draw = nextInspirationDraw(curatedCards, { version: 1, seenIds: ids(old), lastBatchIds: ids(old.slice(-6)) }, () => .4);
  assert.ok(draw.cards.every(card => !ids(old).includes(card.id)));
  assert.equal(draw.history.seenIds.length, 48);
  for (const size of [0, 1, 2, 7]) {
    const pool = curatedCards.slice(0, size);
    let history = blankHistory(); const round = [];
    while (round.length < size) { const draw = nextInspirationDraw(pool, history); round.push(...ids(draw.cards)); history = draw.history; }
    assert.equal(new Set(round).size, size);
    assert.equal(nextInspirationDraw(pool, history).cards.length, Math.min(6, size));
  }
});

test('saved history accepts only current curated IDs and tolerates corrupt or obsolete values', () => {
  for (const raw of [null, '', '{', 'null', '[]', 'x'.repeat(100_001), '{"version":2,"seenIds":[],"lastBatchIds":[]}']) {
    assert.deepEqual(parseInspirationHistory(raw, curatedCards), blankHistory());
  }
  const raw = JSON.stringify({ version: 1, seenIds: [curatedCards[0].id, curatedCards[0].id, 'personal:secret', 'curated:removed', null], lastBatchIds: ids(curatedCards.slice(0, 8)), other: 'must not be persisted' });
  const history = parseInspirationHistory(raw, curatedCards);
  assert.deepEqual(history.seenIds, [curatedCards[0].id]);
  assert.equal(history.lastBatchIds.length, 6);
  assert.deepEqual(Object.keys(history), ['version', 'seenIds', 'lastBatchIds']);
});

test('drawing has no persistence side effects and committing the same render twice is idempotent', () => {
  const storage = memoryStorage();
  const explorer = createInspirationExplorer(curatedCards, () => storage, () => 'curated');
  const firstRender = explorer.draw(() => .5);
  const strictRender = explorer.draw(() => .5);
  assert.deepEqual(ids(firstRender.cards), ids(strictRender.cards));
  assert.equal(storage.writes, 0);
  explorer.commit(firstRender); explorer.commit(firstRender);
  assert.equal(JSON.parse(storage.getItem('curated')).seenIds.length, 6);
  const discarded = explorer.draw(() => .5);
  const next = explorer.draw(() => .5);
  assert.deepEqual(ids(discarded.cards), ids(next.cards));
  assert.equal(JSON.parse(storage.getItem('curated')).seenIds.length, 6);
  explorer.commit(next);
  assert.equal(JSON.parse(storage.getItem('curated')).seenIds.length, 12);
});

test('a recreated page or another mounted explorer continues the saved round without forgetting other preferences', () => {
  const storage = memoryStorage(); storage.values.set('review-progress', 'unchanged');
  const first = createInspirationExplorer(curatedCards, () => storage, () => 'curated');
  const a = first.draw(); first.commit(a);
  const second = createInspirationExplorer(curatedCards, () => storage, () => 'curated');
  const b = second.draw(); second.commit(b);
  const c = first.draw(); first.commit(c);
  assert.equal(new Set([...ids(a.cards), ...ids(b.cards), ...ids(c.cards)]).size, 18);
  assert.equal(JSON.parse(storage.getItem('curated')).seenIds.length, 18);
  assert.equal(storage.getItem('review-progress'), 'unchanged');
});

test('blocked getters, read failures, write failures and silently ignored writes preserve in-memory rotation', () => {
  const storageCases = [
    () => { throw new Error('blocked property'); },
    () => null,
    () => ({ getItem() { throw new Error('blocked read'); }, setItem() {} }),
    () => ({ getItem() { return null; }, setItem() { throw new Error('quota'); } }),
    () => ({ getItem() { return null; }, setItem() {} }),
  ];
  for (const storage of storageCases) {
    const explorer = createInspirationExplorer(curatedCards, storage, () => 'curated');
    const all = [];
    for (let i = 0; i < 5; i++) { const draw = explorer.draw(() => .5); all.push(...ids(draw.cards)); explorer.commit(draw); }
    assert.equal(new Set(all).size, 30);
  }
});

test('storage scopes are isolated and malformed saved state recovers without touching learning keys', () => {
  const storage = memoryStorage(); storage.values.set('one', '{broken');
  storage.values.set('learning-state', '{"keep":true}');
  const one = createInspirationExplorer(curatedCards, () => storage, () => 'one');
  const a = one.draw(() => .5); one.commit(a);
  const two = createInspirationExplorer(curatedCards, () => storage, () => 'two');
  const b = two.draw(() => .5); two.commit(b);
  assert.deepEqual(ids(a.cards), ids(b.cards));
  assert.equal(JSON.parse(storage.getItem('one')).seenIds.length, 6);
  assert.equal(JSON.parse(storage.getItem('two')).seenIds.length, 6);
  assert.equal(storage.getItem('learning-state'), '{"keep":true}');
});
