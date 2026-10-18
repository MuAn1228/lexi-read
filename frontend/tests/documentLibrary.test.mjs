import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/documentLibrary.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { articleEdit, activeArticleList, initialArticleSelection, libraryPath } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
test('article tags preserve existing punctuation, trim additions, and deduplicate without losing first spelling', () => {
  assert.deepEqual(articleEdit('  Learning 🌱  ', ['Science', 'one,two'], ' science, 阅读， 阅读, Exam ').patch, {
    title: 'Learning 🌱', tags: ['Science', 'one,two', '阅读', 'Exam'],
  });
  assert.match(articleEdit('  ', [], '').error, /标题/);
  assert.match(articleEdit('title\nline', [], '').error, /控制字符/);
  assert.match(articleEdit('title', [], 'a\tb').error, /控制字符/);
  assert.equal(articleEdit('🌱'.repeat(250), [], '🌱'.repeat(20)).error, undefined);
  assert.match(articleEdit('🌱'.repeat(251), [], '').error, /250/);
  assert.match(articleEdit('title', [], '🌱'.repeat(21)).error, /20/);
  assert.match(articleEdit('title', Array.from({ length: 11 }, (_, i) => String(i)), '').error, /10/);
  assert.deepEqual(articleEdit('title', ['tag'], 'TAG').patch.tags, ['tag']);
});
test('library queries combine full text, Unicode tags, source, order, and recycle scope with proper URL encoding', () => {
  const path = libraryPath({ q: '  a&b # read  ', tag: '科学 & art', source: 'text', sort: 'oldest', deleted: true });
  const url = new URL(path, 'https://example.test');
  assert.equal(url.pathname, '/documents');
  assert.deepEqual(Object.fromEntries(url.searchParams), { sort: 'oldest', q: 'a&b # read', tag: '科学 & art', source: 'text', deleted: 'true' });
  assert.equal(libraryPath({ q: ' ', tag: '', source: '', sort: 'title', deleted: false }), '/documents?sort=title');
});
test('confirmed summary updates remove recycled articles and restore in canonical order without mutating snapshots', () => {
  const first = { id: 2, title: 'Latest', created_at: '2026-10-17', deleted_at: null };
  const older = { id: 1, title: 'Older', created_at: '2026-10-16', deleted_at: null };
  const original = [first, older];
  assert.deepEqual(activeArticleList(original, { ...first, deleted_at: '2026-10-17' }), [older]);
  assert.equal(original.length, 2);
  assert.equal(original[0].title, 'Latest');
  const updated = activeArticleList(original, { ...older, title: 'Renamed', tags: ['science'] });
  assert.equal(updated[1].title, 'Renamed');
  assert.equal(original[1].title, 'Older');
  assert.deepEqual(activeArticleList([older], first).map(value => value.id), [2, 1]);
  assert.deepEqual(activeArticleList([first], older).map(value => value.id), [2, 1]);
});
test('review occurrence wins over bookmark, while normal opens preserve an exact repeated occurrence', () => {
  const document = { bookmark_occurrence_id: 3, words: [{ id: 1, word: 'Read' }, { id: 2, word: 'this' }, { id: 3, word: 'Read' }] };
  assert.equal(initialArticleSelection(document), 3);
  assert.equal(initialArticleSelection(document, 1), 1);
  assert.equal(initialArticleSelection(document, 99), 3);
  assert.equal(initialArticleSelection({ ...document, bookmark_occurrence_id: 99 }), 1);
  assert.equal(initialArticleSelection({ bookmark_occurrence_id: 3, words: [] }), null);
});
