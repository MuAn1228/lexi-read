import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/reviewExercise.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { reviewCloze, matchesReviewAnswer, reviewPracticeKey } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const occurrence = { id: 9, document_id: 4, document_title: 'Reading', sentence: '🌍 We learned as others learned.', word: 'learned', target_start: 23, target_end: 30 };

test('cloze hides only the intended repeated occurrence using Unicode codepoint offsets', () => {
  const start = Array.from(occurrence.sentence).join('').lastIndexOf('learned') - 1;
  const item = { ...occurrence, target_start: start, target_end: start + 7 };
  const cloze = reviewCloze(item);
  assert.deepEqual(cloze, { before: '🌍 We learned as others ', answer: 'learned', after: '.' });
  assert.equal(cloze.before + cloze.answer + cloze.after, item.sentence);
  assert.deepEqual(reviewCloze({ ...item, target_start: 5, target_end: 12 }), { before: '🌍 We ', answer: 'learned', after: ' as others learned.' });
});

test('cloze rejects absent, partial, fractional, reversed, out-of-bounds and mismatching target ranges', () => {
  assert.equal(reviewCloze(null), null);
  const valid = { ...occurrence, sentence: 'We learned.', target_start: 3, target_end: 10 };
  assert.notEqual(reviewCloze(valid), null);
  for (const change of [
    { target_start: undefined, target_end: undefined }, { target_start: null }, { target_end: null },
    { target_start: 3.5 }, { target_start: -1 }, { target_end: 99 }, { target_end: 3 },
    { target_start: 4 }, { word: 'learn' }, { word: '' }, { sentence: '' },
  ]) assert.equal(reviewCloze({ ...valid, ...change }), null, JSON.stringify(change));
});

test('answer matching allows casing, outside whitespace and typographic apostrophes but requires original word form', () => {
  assert.equal(matchesReviewAnswer(' LEARNED\n', 'learned'), true);
  assert.equal(matchesReviewAnswer('learn', 'learned'), false);
  assert.equal(matchesReviewAnswer('Learned.', 'learned'), false);
  assert.equal(matchesReviewAnswer('lear ned', 'learned'), false);
  assert.equal(matchesReviewAnswer("DON'T", 'don’t'), true);
  assert.equal(matchesReviewAnswer('don‘t', "don't"), true);
  assert.equal(matchesReviewAnswer('cafe\u0301', 'café'), true);
  assert.equal(matchesReviewAnswer(' \t', ''), false);
});

test('practice identity changes with word, occurrence, sentence, offsets, vocabulary and mode', () => {
  const key = reviewPracticeKey(6, occurrence, 'cloze');
  for (const change of [{ id: 10 }, { word: 'Learned' }, { sentence: 'Other context.' }, { target_start: 5 }, { target_end: 12 }]) {
    assert.notEqual(reviewPracticeKey(6, { ...occurrence, ...change }, 'cloze'), key);
  }
  assert.notEqual(reviewPracticeKey(7, occurrence, 'cloze'), key);
  assert.notEqual(reviewPracticeKey(6, occurrence, 'recall'), key);
  assert.notEqual(reviewPracticeKey(6, null, 'cloze'), key);
  assert.equal(reviewPracticeKey(6, { ...occurrence }, 'cloze'), key);
});
