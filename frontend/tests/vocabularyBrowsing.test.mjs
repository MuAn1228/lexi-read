import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/vocabularyBrowsing.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { emptyBrowsingPreferences, readBrowsingPreferences, writeBrowsingPreferences } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const storage = () => {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
};

test('resume state preserves Chinese search, both filters, page, position and open records without touching another scope', () => {
  const saved = emptyBrowsingPreferences();
  saved.resumeVocabulary = true;
  saved.vocabulary = { query: '坚持', filter: 'UNCERTAIN', sourceFilter: 'MAIMEMO_WEAK', page: 7, scrollY: 3456, expanded: [15, 16] };
  const store = storage();
  assert.equal(writeBrowsingPreferences('mine', saved, store), true);
  assert.deepEqual(readBrowsingPreferences('mine', store), { preferences: saved, available: true });
  assert.deepEqual(readBrowsingPreferences('other', store).preferences, emptyBrowsingPreferences());
});

test('old, broken and excessively large preferences fall back without blocking the app', () => {
  const store = storage();
  for (const raw of ['{', 'null', '[]', '{"version":2,"vocabulary":{}}', ' '.repeat(20_001)]) {
    store.setItem('key', raw);
    assert.deepEqual(readBrowsingPreferences('key', store), { preferences: emptyBrowsingPreferences(), available: true });
  }
});

test('invalid filters and positions are sanitized while safe preferences survive', () => {
  const store = storage();
  store.setItem('key', JSON.stringify({ version: 1, resumeVocabulary: 'true', vocabulary: {
    query: 'x'.repeat(1001), filter: 'INVALID', sourceFilter: '__proto__', page: -1, scrollY: 1.5,
    expanded: [1, 1, 2, -1, '3', null, {}, 0, 1e99],
  } }));
  const result = readBrowsingPreferences('key', store).preferences;
  assert.equal(result.resumeVocabulary, false);
  assert.deepEqual(result.vocabulary, { query: 'x'.repeat(1000), filter: 'ALL', sourceFilter: 'ALL', page: 0, scrollY: 0, expanded: [1, 2] });
});

test('blocked reads and full storage are optional failures and never throw', () => {
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('full'); } };
  assert.deepEqual(readBrowsingPreferences('key', blocked), { preferences: emptyBrowsingPreferences(), available: false });
  assert.equal(writeBrowsingPreferences('key', emptyBrowsingPreferences(), blocked), false);
});
