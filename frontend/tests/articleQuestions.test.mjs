import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/articleQuestions.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { articleQuestionProblem, articleQuestionHistory, validArticleFocus, highlightParts } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('question validation trims surrounding whitespace and counts Unicode codepoints', () => {
  assert.match(articleQuestionProblem(' \n\t '), /输入/);
  assert.equal(articleQuestionProblem('  ' + '🌍'.repeat(500) + '\n'), '');
  assert.match(articleQuestionProblem('🌍'.repeat(501)), /500/);
  assert.equal(articleQuestionProblem('<img src=x onerror=alert(1)> Why?'), '');
});

test('follow-ups include only the last three supported AI answers, without mutating displayed history', () => {
  const turns = Array.from({ length: 7 }, (_, id) => ({ id, question: `Question ${id}`, response: { source: 'llm', answerable: true, answer: `Answer ${id}` } }));
  turns[2].response = { source: 'unavailable', answerable: null, answer: null };
  turns[4].response = { source: 'llm', answerable: false, answer: 'No evidence.' };
  turns[6].response = { source: 'llm', answerable: true, answer: ' ' };
  const snapshot = structuredClone(turns);
  assert.deepEqual(articleQuestionHistory(turns), [
    { question: 'Question 1', answer: 'Answer 1' },
    { question: 'Question 3', answer: 'Answer 3' },
    { question: 'Question 5', answer: 'Answer 5' },
  ]);
  assert.deepEqual(turns, snapshot);
  assert.deepEqual(articleQuestionHistory([]), []);
});

test('quote focus verifies exact Unicode source positions rather than the first repeated phrase', () => {
  const document = { ocr_text: '🌍 中文 Read. Read.\n', source: 'web', pages: [] };
  const focus = { text: 'Read.', start: 11, end: 16, token: 1 };
  assert.equal(validArticleFocus(document, focus), true);
  assert.equal(validArticleFocus(document, { ...focus, start: 10, end: 15 }), false);
  assert.equal(validArticleFocus(document, { ...focus, text: 'read.' }), false);
  assert.equal(validArticleFocus(document, { ...focus, end: 100 }), false);
  assert.equal(validArticleFocus(document, { ...focus, start: 11.1 }), false);
  assert.equal(validArticleFocus(document, { ...focus, start: true }), false);
  assert.equal(validArticleFocus(document, null), false);
  const parts = highlightParts(document.ocr_text, 0, focus);
  assert.equal(parts.map(part => part.text).join(''), document.ocr_text);
  assert.deepEqual(parts.filter(part => part.highlighted), [{ text: 'Read.', highlighted: true }]);
  assert.equal(parts[0].text, '🌍 中文 Read. ');
});

test('PDF quote ranges must fit a single real page and highlights use global fragment offsets', () => {
  const document = { ocr_text: 'Page one.\n\n🌱 Page two.', source: 'pdf', pages: [{ number: 1, text_start: 0, text_end: 9 }, { number: 2, text_start: 11, text_end: 22 }] };
  const focus = { text: 'Page two.', start: 13, end: 22, token: 1 };
  assert.equal(validArticleFocus(document, focus), true);
  assert.equal(validArticleFocus(document, { text: 'one.\n\n🌱 Page', start: 5, end: 17, token: 2 }), false);
  const parts = highlightParts('🌱 Page two.', 11, focus);
  assert.deepEqual(parts, [{ text: '🌱 ', highlighted: false }, { text: 'Page two.', highlighted: true }]);
  assert.deepEqual(highlightParts('Page one.', 0, focus), [{ text: 'Page one.', highlighted: false }]);
});

test('highlights preserve literal markup, whitespace and word fragments across multiple render elements', () => {
  const original = '<script>Read\n  that.</script>';
  const fragments = ['<script>', 'Read', '\n  ', 'that', '.</script>'];
  let offset = 0;
  const split = fragments.flatMap(text => {
    const parts = highlightParts(text, offset, { start: 8, end: 19 });
    offset += Array.from(text).length;
    return parts;
  });
  assert.equal(split.map(part => part.text).join(''), original);
  assert.equal(split.filter(part => part.highlighted).map(part => part.text).join(''), 'Read\n  that');
  assert.deepEqual(highlightParts(original, 0, null), [{ text: original, highlighted: false }]);
  assert.deepEqual(highlightParts(original, 0, { start: -50, end: -10 }), [{ text: original, highlighted: false }]);
});
