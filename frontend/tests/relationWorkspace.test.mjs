import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/relationWorkspace.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { relationWorkspacePath, findRelationSource, relationSourceSuggestions, relationRecallMeaning, relationPracticeItems, relationPageNumbers } =
  await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const pair = (patch = {}) => ({ id: 1, source_word: 'adapt', target_word: 'resilient', relation_type: 'DOMAIN_RELATED',
  meaning: 'adj. 有韧性的', reason: '在变化中恢复。', useful: null, ...patch });
const vocabulary = [
  { id: 1, word: 'Adapt', lemma: 'adapt', status: 'KNOWN' },
  { id: 2, word: 'resilient', lemma: 'resilient', status: 'UNCERTAIN' },
  { id: 3, word: 'resilience', lemma: 'resilience', status: 'UNKNOWN' },
  { id: 4, word: 'adapting', lemma: 'adapt', status: null },
];

test('collection filters are encoded as one query value and bounded to the API search limit', () => {
  const path = relationWorkspacePath({ q: ' adapt&scope=all ', relation: 'SYNONYM', scope: 'unfamiliar', include_dismissed: true, page: 8 });
  const url = new URL(path, 'https://example.test');
  assert.equal(url.pathname, '/relation-workspace');
  assert.deepEqual(Object.fromEntries(url.searchParams), { q: 'adapt&scope=all', relation: 'SYNONYM', scope: 'unfamiliar', include_dismissed: 'true', page: '8', page_size: '12' });
  assert.equal(url.searchParams.getAll('scope').length, 1);
  assert.equal(new URL(relationWorkspacePath({ q: 'a'.repeat(120), relation: 'all', scope: 'all', include_dismissed: false, page: 1 }), url).searchParams.get('q').length, 100);
});

test('exploration resolves only owned entries and prioritises unfamiliar source suggestions', () => {
  assert.equal(findRelationSource(vocabulary, ' ADAPT '), vocabulary[0]);
  assert.equal(findRelationSource(vocabulary, 'adapting'), vocabulary[3]);
  assert.equal(findRelationSource(vocabulary, 'invented'), null);
  assert.equal(findRelationSource(vocabulary, ''), null);
  assert.deepEqual(relationSourceSuggestions(vocabulary, '').map(item => item.id), [3, 2, 4, 1]);
  assert.deepEqual(relationSourceSuggestions(vocabulary, 'adapt', 2).map(item => item.id), [1, 4]);
  assert.deepEqual(relationSourceSuggestions(vocabulary, 'resil').map(item => item.id), [3, 2]);
  assert.deepEqual(relationSourceSuggestions(vocabulary, 'no match'), []);
  assert.deepEqual(vocabulary.map(item => item.id), [1, 2, 3, 4]);
});

test('recall requires a real Chinese snapshot and never uses dismissed or empty pairs', () => {
  assert.equal(relationRecallMeaning(pair()), 'adj. 有韧性的');
  for (const patch of [{ useful: false }, { meaning: null }, { meaning: ' ' }, { meaning: 'strong and flexible' },
    { source_word: '' }, { target_word: 'ADAPT' }]) assert.equal(relationRecallMeaning(pair(patch)), null);
  // A lost feedback batch does not invalidate an actual saved explanation.
  assert.equal(relationPracticeItems([pair({ batch_id: null })]).length, 1);
});

test('recall masks the English answer in saved glosses including mixed case and regex punctuation', () => {
  assert.equal(relationRecallMeaning(pair({ meaning: 'Resilient：adj. 有韧性的，resilient 指恢复能力' })), '____：adj. 有韧性的，____ 指恢复能力');
  assert.equal(relationRecallMeaning(pair({ target_word: 'look after', meaning: 'look after：照顾；LOOK AFTER sb.' })), '____：照顾；____ sb.');
  assert.equal(relationRecallMeaning(pair({ target_word: 'a+b', meaning: 'a+b：含义' })), '____：含义');
});

test('practice snapshots keep page order, remove duplicate pairs, and keep distinct relation types', () => {
  const first = pair();
  const other = pair({ id: 2, relation_type: 'SYNONYM' });
  const result = relationPracticeItems([first, pair({ id: 99, source_word: 'ADAPT', target_word: 'RESILIENT' }), pair({ useful: false }), other]);
  assert.deepEqual(result, [first, other]);
});

test('pagination keeps endpoints and the current page without flooding a long collection', () => {
  assert.deepEqual(relationPageNumbers(1, 1), [1]);
  assert.deepEqual(relationPageNumbers(1, 50), [1, 2, 3, 4, 5, 'after', 50]);
  assert.deepEqual(relationPageNumbers(25, 50), [1, 'before', 24, 25, 26, 'after', 50]);
  assert.deepEqual(relationPageNumbers(50, 50), [1, 'before', 46, 47, 48, 49, 50]);
  for (let current = 1; current <= 100; current++) {
    const numbers = relationPageNumbers(current, 100).filter(value => typeof value === 'number');
    assert.equal(new Set(numbers).size, numbers.length);
    assert.ok(numbers.includes(1) && numbers.includes(100) && numbers.includes(current));
    assert.ok(numbers.every(value => value > 0 && value <= 100));
  }
});
