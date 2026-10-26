import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/dictionaryLookup.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { normalizeLookupQuery, lookupQueryProblem, pushLookupHistory } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

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
