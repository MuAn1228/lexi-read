import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/articleVocabularyExport.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const sheet = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const items = [
  { word: 'approach', lemma: 'approach', article_word: 'approached', phonetic: '/əˈprəʊtʃ/', meanings: ['v. 接近；靠近', 'n. 方法'], count: 3, first_occurrence_id: 9 },
  { word: 'quietly', lemma: 'quietly', article_word: 'quietly', phonetic: null, meanings: [], count: 1, first_occurrence_id: 12 },
];

test('article vocabulary sheet uses one document-level endpoint', () => {
  assert.equal(sheet.articleVocabularySheetPath(42), '/documents/42/vocabulary-sheet');
});

test('selected words keep backend first-occurrence order', () => {
  assert.deepEqual(sheet.selectedVocabularySheetItems(items, new Set([12])), [items[1]]);
  assert.deepEqual(sheet.selectedVocabularySheetItems(items, new Set([12, 9])), items);
});

test('portable TXT includes pronunciation, complete meanings, counts and empty-definition fallback', () => {
  const output = sheet.articleVocabularySheetText('A New Day', items);
  assert.ok(output.includes('待收录词汇表 · 2 个词 · 按文章首次出现排序'));
  assert.ok(output.includes('001. approach /əˈprəʊtʃ/  v. 接近；靠近；n. 方法 〔出现 3 次〕'));
  assert.ok(output.includes('002. quietly  暂无本地释义'));
  assert.ok(output.endsWith('\r\n'));
});

test('TXT normalizes newlines and creates a Windows-safe filename', () => {
  const output = sheet.articleVocabularySheetText('  Reading\nOne ', [{ ...items[0], meanings: ['v. 接近\n或靠近'] }]);
  assert.ok(output.startsWith('Reading One\r\n'));
  assert.ok(output.includes('v. 接近 或靠近'));
  assert.equal(sheet.articleVocabularySheetFilename(' Lesson: 1 / Growth? '), 'Lesson- 1 - Growth-待收录词汇表.txt');
  assert.equal(sheet.articleVocabularySheetFilename(' ..<>|  '), '当前文章-待收录词汇表.txt');
});
