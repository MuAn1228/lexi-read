import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

async function load(name) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
}
const { hasCorrectedImageText, isImageArticle } = await load('imageArticle');
const { articleParts } = await load('textArticle');
const occurrence = (id, word, start, end) => ({ id, word, text_start: start, text_end: end, bbox_source: 'corrected_text', bbox: null, confidence: null });

test('uploaded and demo corrections open as text while uncorrected images and other sources retain their reader', () => {
  const corrected = occurrence(1, 'Read', 0, 4);
  for (const source of ['upload', 'demo']) {
    assert.equal(isImageArticle(source), true);
    assert.equal(hasCorrectedImageText({ source, words: [corrected] }), true);
    assert.equal(hasCorrectedImageText({ source, words: [{ ...corrected, bbox_source: 'paddle' }] }), false);
    assert.equal(hasCorrectedImageText({ source, words: [] }), false);
  }
  for (const source of ['pdf', 'text', 'web', 'unknown']) {
    assert.equal(isImageArticle(source), false);
    assert.equal(hasCorrectedImageText({ source, words: [corrected] }), false);
  }
});

test('corrected image text selects repeated words by exact Unicode offsets and never needs image boxes', () => {
  const text = '🌍 中文 Read, read!\n\n\tRead again.';
  const words = [...text.matchAll(/Read|read|again/g)].map((match, index) => {
    const start = Array.from(text.slice(0, match.index)).length;
    return occurrence(index + 11, match[0], start, start + Array.from(match[0]).length);
  });
  const parts = articleParts(text, words.slice().reverse());
  assert.equal(parts.map(part => part.text).join(''), text);
  assert.deepEqual(parts.filter(part => part.occurrence).map(part => part.occurrence.id), [11, 12, 13, 14]);
  assert.deepEqual(parts.filter(part => part.occurrence).map(part => part.text), ['Read', 'read', 'Read', 'again']);
  assert.ok(parts.filter(part => part.occurrence).every(part => part.occurrence.bbox === null));
  // Old OCR IDs or offsets must not be attached to a different corrected word.
  const invalid = articleParts(text, [{ ...words[0], word: 'Removed' }]);
  assert.equal(invalid.map(part => part.text).join(''), text);
  assert.equal(invalid.filter(part => part.occurrence).length, 0);
});
