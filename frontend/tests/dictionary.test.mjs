import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/dictionary.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { matchesVocabularyQuery, dictionaryPath, dictionaryAnnotationsPath, dictionaryGenerationLabel, hasMaimemoAnnotations } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const word = { word: 'Banks', lemma: 'bank', meaning_summary: 'n. 银行；河岸\nv. 将钱存入银行' };

test('Chinese meaning search finds all dictionary senses alongside word and lemma search', () => {
  for (const query of ['银行', '河岸', '存入', ' banks ', 'BANK', '\t']) assert.equal(matchesVocabularyQuery(word, query), true);
  assert.equal(matchesVocabularyQuery(word, '河流'), false);
  assert.equal(matchesVocabularyQuery({ ...word, meaning_summary: null }, '河岸'), false);
  assert.equal(matchesVocabularyQuery({ word: 'Bank', lemma: 'bank' }, 'bank'), true);
});

test('dictionary paths encode punctuation and never let word text change the endpoint', () => {
  assert.equal(dictionaryPath(' Bank '), '/dictionary/Bank');
  assert.equal(dictionaryPath('well-being', true), '/dictionary/well-being/explain');
  for (const value of ['a/b', 'a?token=1#more', '银行', 'a%2Fb']) {
    const path = dictionaryPath(value, true);
    assert.equal(path.split('/').length, 4);
    assert.equal(path.split('/')[3], 'explain');
    assert.equal(decodeURIComponent(path.split('/')[2]), value);
  }
});

test('generation labels distinguish a cached explanation from an unavailable AI service', () => {
  assert.equal(dictionaryGenerationLabel({ detail: null, can_generate: true, cached: false }), '生成详细讲解');
  assert.equal(dictionaryGenerationLabel({ detail: null, can_generate: false, cached: false }), 'AI 详细讲解暂不可用');
  assert.equal(dictionaryGenerationLabel({ detail: {}, can_generate: false, cached: true }), '已保存详细讲解');
  assert.equal(dictionaryGenerationLabel({ detail: {}, can_generate: true, cached: false }), '详细讲解已生成');
});

test('personal annotation endpoints keep punctuation inside the word segment', () => {
  assert.equal(dictionaryAnnotationsPath(' Bank '), '/dictionary/Bank/annotations');
  for (const value of ['a/b', 'a?token=1#more', 'a%2Fb']) {
    const path = dictionaryAnnotationsPath(value);
    assert.equal(path.split('/').length, 4);
    assert.equal(path.split('/')[3], 'annotations');
    assert.equal(decodeURIComponent(path.split('/')[2]), value);
  }
});

test('Maimemo personal content is separate from connection status or an empty built-in dictionary assumption', () => {
  const empty = { interpretations: [], phrases: [], notes: [], status: 'available' };
  assert.equal(hasMaimemoAnnotations(null), false);
  assert.equal(hasMaimemoAnnotations(undefined), false);
  assert.equal(hasMaimemoAnnotations(empty), false);
  for (const kind of ['interpretations', 'phrases', 'notes']) {
    assert.equal(hasMaimemoAnnotations({ ...empty, status: 'unavailable', stale: true, [kind]: [{ text: '上次保存的个人注解' }] }), true);
  }
});
