import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/textArticle.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { articleParts, characterCount, normalizeArticle, textImportProblem } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const occurrence = (id, word, start, end) => ({ id, word, lemma: word.toLowerCase(), sentence: '', bbox: null, line: 0, confidence: null, bbox_source: 'text', status: null, text_start: start, text_end: end });
const originalText = parts => parts.map(part => part.text).join('');

test('codepoint offsets preserve emoji, Chinese, repeated words, punctuation, tabs and blank paragraphs', () => {
  const text = '🪴 中文: Read, read!\n\n\tRead?';
  const points = Array.from(text);
  const matches = [...text.matchAll(/Read|read/g)];
  const words = matches.map((match, index) => {
    const start = Array.from(text.slice(0, match.index)).length;
    return occurrence(index + 1, match[0], start, start + 4);
  });
  assert.notEqual(points.length, text.length);
  const parts = articleParts(text, words.slice().reverse());
  assert.equal(originalText(parts), text);
  assert.deepEqual(parts.filter(part => part.occurrence).map(part => part.occurrence.id), [1, 2, 3]);
  assert.deepEqual(parts.filter(part => part.occurrence).map(part => part.text), ['Read', 'read', 'Read']);
  assert.equal(parts.at(-3).text, '!\n\n\t');
  assert.equal(parts.at(-1).text, '?');
});

test('apostrophes, hyphens, and literal HTML stay exactly as the source article', () => {
  const text = `<script>Don't re-read.</script> <img src=x onerror=alert(1)>`;
  const words = [occurrence(1, "Don't", 8, 13), occurrence(2, 're-read', 14, 21)];
  const parts = articleParts(text, words);
  assert.equal(originalText(parts), text);
  assert.deepEqual(parts.filter(part => part.occurrence).map(part => part.text), ["Don't", 're-read']);
  assert.equal(parts[0].text, '<script>');
});

test('invalid, overlapping, mismatched, and duplicate offsets cannot discard or replace article characters', () => {
  const text = 'Read this, read that.';
  const words = [
    occurrence(1, 'Read', 0, 4), occurrence(2, 'this', 5, 9), occurrence(3, 'read', 11, 15),
    occurrence(4, 'fake', 16, 20), occurrence(5, 'ead', 1, 4), occurrence(6, 'Read', -1, 4),
    occurrence(7, 'that', 16, 99), occurrence(8, 'Read', 0.1, 4), occurrence(9, 'Read', null, null),
    occurrence(10, 'Read', undefined, undefined), occurrence(11, '', 10, 10), occurrence(3, 'that', 16, 20),
  ];
  const parts = articleParts(text, words);
  assert.equal(originalText(parts), text);
  assert.deepEqual(parts.filter(part => part.occurrence).map(part => part.occurrence.id), [1, 2, 3]);
  assert.equal(parts.at(-1).text, ' that.');
  assert.deepEqual(articleParts(text, [occurrence(99, 'Not in text', 0, 4)]), [{ text, occurrence: null }]);
  assert.deepEqual(articleParts('', []), []);
});

test('no offset metadata keeps the whole original article readable', () => {
  const text = 'Read\n  this.\n';
  assert.deepEqual(articleParts(text, [occurrence(1, 'Read', null, null)]), [{ text, occurrence: null }]);
});

test('validation and counters use normalized Unicode codepoints rather than UTF-16 code units', () => {
  assert.equal(normalizeArticle('Read\r\n\rthis\n'), 'Read\n\nthis\n');
  assert.equal(characterCount('🪴🌍🧠'), 3);
  assert.equal(textImportProblem({ title: '', text: 'A' + '🪴'.repeat(19_999) }), '');
  assert.match(textImportProblem({ title: '', text: 'A' + '🪴'.repeat(20_000) }), /20,000/);
  assert.equal(textImportProblem({ title: '🌍'.repeat(250), text: 'Read.' }), '');
  assert.match(textImportProblem({ title: '🌍'.repeat(251), text: 'Read.' }), /250/);
  assert.match(textImportProblem({ title: '', text: '\n\t  ' }), /请先粘贴/);
  assert.match(textImportProblem({ title: '', text: '只含中文。🪴' }), /英文单词/);
  assert.match(textImportProblem({ title: 'title\nline', text: 'Read.' }), /控制字符/);
});
