import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/wordExpansion.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { expansionOptions, expansionLabel, expansionPath, expansionRequestBody, expansionCacheKey, isExpansionRecommendation, cacheExpansion, expansionSourceLabel } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

function recommendation(relation = 'synonym', overrides = {}) {
  return {
    batch_id: 31, source: 'llm', warning: null,
    items: [{ word: 'glad', meaning: 'adj. 高兴的', reason: '与 happy 意思相近；glad 常用于表达对某件事感到欣慰。', relation, score: 0.8, priority: 0.65 }],
    ...overrides,
  };
}

test('expansion offers three main memory links before contrast and collocations', () => {
  assert.deepEqual(expansionOptions.map(option => option.label), ['同类词', '近义词', '派生词', '反义词', '常用搭配']);
  assert.deepEqual(expansionOptions.filter(option => option.primary).map(option => option.relation), ['domain_related', 'synonym', 'word_family']);
  assert.equal(expansionLabel('word_family'), '派生词');
  assert.equal(expansionLabel('domain_related'), '同类词');
  assert.match(expansionOptions.find(option => option.relation === 'word_family').description, /复数、过去式.*不算派生词/);
});

test('expansion path keeps punctuation and query-like text inside the word segment', () => {
  assert.equal(expansionPath(' happy '), '/vocabulary/happy/recommend');
  for (const word of ['well-being', 'a/b', 'a?token=1#more', 'a%2Fb', '词族', 'look up']) {
    const path = expansionPath(word);
    assert.equal(path.split('/').length, 4);
    assert.equal(path.split('/')[3], 'recommend');
    assert.equal(decodeURIComponent(path.split('/')[2]), word);
  }
  assert.throws(() => expansionPath(' \t\n '), /先选择一个单词/);
});

test('cache identity ignores case and edge whitespace but separates word and category', () => {
  assert.equal(expansionCacheKey(' Happy ', 'synonym'), expansionCacheKey('happy', 'synonym'));
  assert.notEqual(expansionCacheKey('happy', 'synonym'), expansionCacheKey('happy', 'domain_related'));
  assert.notEqual(expansionCacheKey('unhappy', 'synonym'), expansionCacheKey('happy', 'synonym'));
  assert.notEqual(expansionCacheKey('a:synonym', 'word_family'), expansionCacheKey('a', 'synonym'));
});

test('discovery and retries reuse saved results; only explicit regeneration bypasses them', () => {
  for (const option of expansionOptions) {
    assert.deepEqual(expansionRequestBody(option.relation), { relation: option.relation, refresh: false });
    assert.deepEqual(expansionRequestBody(option.relation, false), { relation: option.relation, refresh: false });
    assert.deepEqual(expansionRequestBody(option.relation, true), { relation: option.relation, refresh: true });
  }
});

test('real response shape accepts nullable warnings and validates each selected category', () => {
  for (const option of expansionOptions) {
    assert.equal(isExpansionRecommendation(recommendation(option.relation), option.relation), true);
    assert.equal(isExpansionRecommendation(recommendation(option.relation, { source: 'offline' }), option.relation), true);
    assert.equal(isExpansionRecommendation(recommendation(option.relation, { warning: 'AI 暂时不可用' }), option.relation), true);
    assert.equal(isExpansionRecommendation(recommendation(option.relation, { cached: true }), option.relation), true);
    assert.equal(isExpansionRecommendation(recommendation(option.relation, { cached: false }), option.relation), true);
  }
  assert.equal(isExpansionRecommendation(recommendation('word_family'), 'synonym'), false);
  assert.equal(isExpansionRecommendation(recommendation('synonym', { items: [] }), 'synonym'), true);
});

test('malformed response never creates a broken or cross-category feedback card', () => {
  const valid = recommendation();
  const item = valid.items[0];
  for (const value of [null, '', [], {}, { ...valid, batch_id: 0 }, { ...valid, batch_id: 1.5 },
    { ...valid, source: 'unavailable' }, { ...valid, warning: { text: 'bad' } },
    ...[null, 'true', 'false', 0, 1, {}].map(cached => ({ ...valid, cached })),
    { ...valid, items: null }, { ...valid, items: Array(4).fill(item) },
    ...[{ word: '' }, { meaning: '' }, { reason: '' }, { relation: 'antonym' },
      { score: NaN }, { priority: Infinity }, { score: -0.01 }, { priority: 1.01 }]
      .map(change => ({ ...valid, items: [{ ...item, ...change }] })),
    { ...valid, items: [item, { ...item, word: ' GLAD ' }] },
  ]) assert.equal(isExpansionRecommendation(value, 'synonym'), false);
});

test('only successful nonempty AI responses are retained per word and category', () => {
  const cache = new Map();
  const valid = recommendation();
  assert.equal(cacheExpansion(cache, 'happy', 'synonym', valid), true);
  assert.equal(cache.get(expansionCacheKey('HAPPY', 'synonym')), valid);
  assert.equal(cacheExpansion(cache, 'happy', 'synonym', recommendation('synonym', { source: 'offline' })), false);
  assert.equal(cacheExpansion(cache, 'happy', 'synonym', recommendation('synonym', { items: [] })), false);
  assert.equal(cacheExpansion(cache, 'happy', 'word_family', valid), false);
  assert.equal(cache.get(expansionCacheKey('happy', 'synonym')), valid);
  assert.equal(cache.size, 1);
});

test('session cache bounds memory and refresh retains the newest result', () => {
  const cache = new Map();
  for (let index = 0; index < 100; index++) cacheExpansion(cache, `word-${index}`, 'synonym', recommendation());
  const refreshed = recommendation('synonym', { batch_id: 200 });
  cacheExpansion(cache, 'word-0', 'synonym', refreshed);
  cacheExpansion(cache, 'word-100', 'synonym', recommendation());
  assert.equal(cache.size, 100);
  assert.equal(cache.has(expansionCacheKey('word-1', 'synonym')), false);
  assert.equal(cache.get(expansionCacheKey('word-0', 'synonym')), refreshed);
});

test('source labels distinguish saved result reuse and session cache without relabelling offline fallbacks', () => {
  assert.equal(expansionSourceLabel(recommendation()), 'AI 词汇拓展');
  assert.equal(expansionSourceLabel(recommendation('synonym', { cached: false })), 'AI 词汇拓展');
  assert.equal(expansionSourceLabel(recommendation(), true), 'AI 词汇拓展 · 本次已生成');
  const reused = recommendation('synonym', { cached: true });
  assert.equal(expansionSourceLabel(reused), 'AI 词汇拓展 · 已复用');
  assert.equal(expansionSourceLabel(reused, true), 'AI 词汇拓展 · 已复用');
  const fallback = recommendation('synonym', { source: 'offline' });
  assert.equal(expansionSourceLabel(fallback), '离线规则推荐');
  assert.equal(expansionSourceLabel(fallback, true), '离线规则推荐');
  assert.equal(expansionSourceLabel({ ...fallback, cached: true }), '离线规则推荐');
});
