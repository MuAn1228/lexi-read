import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/wordInspiration.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { curatedCards, personalInspirationCards, pickInspirationBatch } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

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

test('offline selection contains many distinct, complete editorial cards rather than the old fixed six', () => {
  assert.ok(curatedCards.length >= 36);
  assert.equal(new Set(ids(curatedCards)).size, curatedCards.length);
  assert.equal(new Set(curatedCards.map(card => card.word)).size, curatedCards.length);
  for (const card of curatedCards) {
    assert.match(card.id, /^curated:/);
    assert.ok(card.word && card.kind && card.meaning && card.sentence);
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
