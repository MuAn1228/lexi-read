import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/dictionaryLookup.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { normalizeLookupQuery, lookupQueryProblem, pushLookupHistory, readLookupHistory, writeLookupHistory } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

function memoryStorage() {
  const values = new Map();
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

test('lookup normalization preserves display spelling and inflections while cleaning supported punctuation and whitespace', () => {
  assert.equal(normalizeLookupQuery(' \tDeep\n  Learning\u00a0 '), 'Deep Learning');
  assert.equal(normalizeLookupQuery('  Learner’s cross–validation  '), "Learner's cross-validation");
  assert.equal(normalizeLookupQuery('isn‘t'), "isn't");
  assert.equal(normalizeLookupQuery('Models'), 'Models');
  assert.equal(normalizeLookupQuery('studies'), 'studies');
  assert.equal(normalizeLookupQuery('https://example.com/a'), 'https://example.com/a');
});

test('valid queries include English words, phrases and apostrophes or hyphens between letters', () => {
  for (const query of ['Bank', 'machine learning', 'state-of-the-art', "don't", "Learner’s Dictionary", 'cross–validation', 'rock-and-roll music']) {
    assert.equal(lookupQueryProblem(query), '', query);
  }
});

test('empty, Chinese, digits, URLs, paths and unsupported punctuation are rejected locally', () => {
  for (const query of ['', ' \t\n ']) assert.match(lookupQueryProblem(query), /请输入/);
  for (const query of [
    '银行', 'bank银行', 'model2', '2', 'https://example.com', '/dictionary/bank', '../bank',
    'a%2Fb', 'a/b', 'a\\b', 'bank?refresh=true', '<script>', 'bank#meaning', 'bank.',
    '-bank', 'bank-', "'bank", "bank'", 'well--being', "a''b", 'a - b', 'café', 'co—operate', 'co‐operate',
  ]) {
    assert.match(lookupQueryProblem(query), /有效英文单词或短语/, query);
  }
});

test('the 150 character bound applies after whitespace and punctuation normalization', () => {
  assert.equal(lookupQueryProblem('a'.repeat(150)), '');
  assert.match(lookupQueryProblem('a'.repeat(151)), /150/);
  assert.equal(lookupQueryProblem(`  ${'a'.repeat(148)}   b  `), '');
  assert.match(lookupQueryProblem(`${'a'.repeat(149)} b`), /150/);
});

test('history promotes a normalized spelling and removes case and curly-apostrophe duplicates', () => {
  const history = Object.freeze(['BANK', 'Learner’s Dictionary', ' Models ', 'bank', "learner's dictionary", 'studies']);
  assert.deepEqual(pushLookupHistory(history, ' learner‘s   DICTIONARY '), ["learner's DICTIONARY", 'BANK', 'Models', 'studies']);
  assert.deepEqual(history, ['BANK', 'Learner’s Dictionary', ' Models ', 'bank', "learner's dictionary", 'studies']);
  assert.deepEqual(pushLookupHistory(['cross–validation', 'Cross-Validation'], 'cross-validation'), ['cross-validation']);
  assert.deepEqual(pushLookupHistory(['model', 'Models'], 'models'), ['models', 'model']);
});

test('invalid new words never enter history and corrupt persisted values are safely discarded', () => {
  assert.deepEqual(pushLookupHistory([' Bank ', null, 42, {}, '银行', '../bank', 'a'.repeat(151), 'BANK', 'Deep\nLearning'], '/bad/path'), ['Bank', 'Deep Learning']);
  assert.deepEqual(pushLookupHistory(null, 'bank'), ['bank']);
  assert.deepEqual(pushLookupHistory({ words: ['bank'] }, ''), []);
  assert.deepEqual(pushLookupHistory(['word'], undefined), ['word']);
});

test('history holds at most twelve distinct valid queries, with the newest query first', () => {
  const words = Array.from({ length: 30 }, (_, index) => `word${String.fromCharCode(97 + Math.floor(index / 26))}${String.fromCharCode(97 + index % 26)}`);
  assert.deepEqual(pushLookupHistory(words, 'new word'), ['new word', ...words.slice(0, 11)]);
  assert.deepEqual(pushLookupHistory(words, words[15].toUpperCase()), [words[15].toUpperCase(), ...words.slice(0, 11)]);
  assert.deepEqual(pushLookupHistory(['bad/path', '', ...words.flatMap(word => [word, word.toUpperCase()])], ''), words.slice(0, 12));
  assert.deepEqual(pushLookupHistory([], ''), []);
});

test('lookup history round trips with the versioned format and keeps newest-first order after reloading', () => {
  const storage = memoryStorage();
  const key = 'dictionary-history:v1:backend:guest';
  assert.deepEqual(readLookupHistory(key, storage), { history: [], available: true });

  let history = pushLookupHistory([], 'Bank');
  history = pushLookupHistory(history, ' Models ');
  history = pushLookupHistory(history, 'Deep Learning');
  assert.equal(writeLookupHistory(key, history, storage), true);
  assert.deepEqual(JSON.parse(storage.getItem(key)), { version: 1, words: ['Deep Learning', 'Models', 'Bank'] });

  const reloadedStorage = { getItem: storage.getItem };
  const reloaded = readLookupHistory(key, reloadedStorage);
  assert.deepEqual(reloaded, { history: ['Deep Learning', 'Models', 'Bank'], available: true });
  const updated = pushLookupHistory(reloaded.history, 'bank');
  assert.equal(writeLookupHistory(key, updated, storage), true);
  assert.deepEqual(readLookupHistory(key, reloadedStorage), { history: ['bank', 'Deep Learning', 'Models'], available: true });

  assert.equal(writeLookupHistory(key, [], storage), true);
  assert.deepEqual(readLookupHistory(key, reloadedStorage), { history: [], available: true });
});

test('history storage keys isolate backend and user histories', () => {
  const storage = memoryStorage();
  const firstKey = 'dictionary-history:v1:backend-one:guest-one';
  const secondKey = 'dictionary-history:v1:backend-two:guest-one';
  const thirdKey = 'dictionary-history:v1:backend-one:guest-two';
  assert.equal(writeLookupHistory(firstKey, ['Bank'], storage), true);
  assert.equal(writeLookupHistory(secondKey, ['Models'], storage), true);
  assert.equal(writeLookupHistory(thirdKey, ['Learning'], storage), true);
  assert.deepEqual(readLookupHistory(firstKey, storage), { history: ['Bank'], available: true });
  assert.deepEqual(readLookupHistory(secondKey, storage), { history: ['Models'], available: true });
  assert.deepEqual(readLookupHistory(thirdKey, storage), { history: ['Learning'], available: true });
  assert.deepEqual(readLookupHistory('missing', storage), { history: [], available: true });
});

test('reading normalizes, validates and deduplicates stored words without reversing history', () => {
  const words = Array.from({ length: 20 }, (_, index) => `word${String.fromCharCode(97 + index)}`);
  const stored = {
    version: 1,
    words: [
      ' Learner’s\n Dictionary ', "learner's dictionary", 'BANK', 'bank', ' cross–validation ',
      null, 42, {}, [], '银行', '../bank', 'model2', 'a'.repeat(151), '', ...words,
    ],
  };
  const result = readLookupHistory('key', { getItem: () => JSON.stringify(stored) });
  assert.deepEqual(result, {
    history: ["Learner's Dictionary", 'BANK', 'cross-validation', ...words.slice(0, 9)],
    available: true,
  });
});

test('malformed JSON, unsupported versions and invalid envelopes are empty available history', () => {
  for (const serialized of [
    null, '', '{', 'undefined', 'null', '0', 'false', '"bank"', '[]', '["bank"]', '{}',
    '{"words":["bank"]}', '{"version":0,"words":["bank"]}', '{"version":2,"words":["bank"]}',
    '{"version":"1","words":["bank"]}', '{"version":1}', '{"version":1,"words":null}',
    '{"version":1,"words":"bank"}', '{"version":1,"words":{"0":"bank"}}',
  ]) {
    assert.deepEqual(readLookupHistory('key', { getItem: () => serialized }), { history: [], available: true }, String(serialized));
  }
  assert.deepEqual(readLookupHistory('key', { getItem: () => '{"version":1,"words":[]}' }), { history: [], available: true });
});

test('the serialized input bound accepts twenty thousand characters and ignores larger data', () => {
  const serialized = JSON.stringify({ version: 1, words: ['Bank'] }).padEnd(20_000, ' ');
  assert.deepEqual(readLookupHistory('key', { getItem: () => serialized }), { history: ['Bank'], available: true });
  assert.deepEqual(readLookupHistory('key', { getItem: () => `${serialized} ` }), { history: [], available: true });
  assert.deepEqual(readLookupHistory('key', { getItem: () => 'x'.repeat(30_000) }), { history: [], available: true });
});

test('writing sanitizes invalid data, keeps display spelling and the first twelve valid distinct words', () => {
  const storage = memoryStorage();
  const words = Array.from({ length: 20 }, (_, index) => `word${String.fromCharCode(97 + index)}`);
  const history = Object.freeze([
    ' Learner‘s Dictionary ', "learner's dictionary", 'BANK', 'bank', ' cross–validation ',
    null, 42, {}, [], '银行', '../bank', 'a'.repeat(151), '', ...words,
  ]);
  assert.equal(writeLookupHistory('key', history, storage), true);
  assert.deepEqual(JSON.parse(storage.getItem('key')), {
    version: 1,
    words: ["Learner's Dictionary", 'BANK', 'cross-validation', ...words.slice(0, 9)],
  });
  assert.equal(history[0], ' Learner‘s Dictionary ');
  assert.equal(history.length, 33);
  assert.equal(writeLookupHistory('key', null, storage), true);
  assert.deepEqual(JSON.parse(storage.getItem('key')), { version: 1, words: [] });
});

test('storage read and write exceptions are reported as unavailable without escaping', () => {
  const denied = () => { throw new Error('Storage access denied'); };
  assert.deepEqual(readLookupHistory('key', { getItem: denied }), { history: [], available: false });
  assert.equal(writeLookupHistory('key', ['Bank'], { setItem: denied }), false);
});

test('default storage supports browser access and safely handles missing or denied localStorage', () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  try {
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: memoryStorage() });
    assert.equal(writeLookupHistory('default-key', ['Bank']), true);
    assert.deepEqual(readLookupHistory('default-key'), { history: ['Bank'], available: true });

    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: undefined });
    assert.deepEqual(readLookupHistory('key'), { history: [], available: false });
    assert.equal(writeLookupHistory('key', ['Bank']), false);

    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() { throw new Error('Browser blocked localStorage'); },
    });
    assert.deepEqual(readLookupHistory('key'), { history: [], available: false });
    assert.equal(writeLookupHistory('key', ['Bank']), false);

    const injectedStorage = memoryStorage();
    assert.equal(writeLookupHistory('injected-key', ['Models'], injectedStorage), true);
    assert.deepEqual(readLookupHistory('injected-key', injectedStorage), { history: ['Models'], available: true });
  } finally {
    if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor);
    else delete globalThis.localStorage;
  }
});
