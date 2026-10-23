import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/vocabularyOperations.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { selectVocabularyPage, vocabularyBatch, validGroupName, normalizeGroupName } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('page selection preserves off-page words and deselection removes only current page', () => {
  const original = new Set([1, 2, 90]);
  const selected = selectVocabularyPage(original, [1, 2, 3], true);
  assert.deepEqual([...selected], [1, 2, 90, 3]);
  assert.deepEqual([...selectVocabularyPage(selected, [1, 2, 3], false)], [90]);
  assert.deepEqual([...original], [1, 2, 90]);
});

test('a fifth page cannot silently exceed 200 and freed slots can be reused', () => {
  let selected = new Set();
  for (let page = 0; page < 5; page++) selected = selectVocabularyPage(selected, Array.from({ length: 50 }, (_, i) => page * 50 + i + 1), true);
  assert.equal(selected.size, 200);
  assert.equal(selected.has(201), false);
  selected = selectVocabularyPage(selected, [1, 2], false);
  selected = selectVocabularyPage(selected, [201, 202, 203], true);
  assert.equal(selected.size, 200);
  assert.equal(selected.has(201), true);
  assert.equal(selected.has(202), true);
  assert.equal(selected.has(203), false);
});

test('batch snapshot carries original versions independent of later vocabulary edits', () => {
  const words = [{ id: 2, updated_at: 'old-2', status: null }, { id: 3, updated_at: 'old-3', status: 'UNKNOWN' }];
  const batch = vocabularyBatch(words, 'KNOWN', 'same-retry-id');
  words[0].updated_at = 'new-2';
  words.push({ id: 4, updated_at: 'new-4', status: null });
  assert.deepEqual(batch, { request_id: 'same-retry-id', status: 'KNOWN', items: [{ id: 2, updated_at: 'old-2' }, { id: 3, updated_at: 'old-3' }] });
});

test('group names trim surrounding whitespace and count Unicode characters', () => {
  assert.equal(normalizeGroupName('  阅读练习  '), '阅读练习');
  for (const value of ['', ' \n ', '词'.repeat(31), '🌱'.repeat(31)]) assert.equal(validGroupName(value), false);
  for (const value of ['词', '词'.repeat(30), '🌱'.repeat(30), '  Reading  ']) assert.equal(validGroupName(value), true);
});
