import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/practiceGeneration.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { practiceProblem, practiceTargetReason, practiceParts, usablePractice } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const target = { id: 1, word: 'learning', lemma: 'learn', status: 'UNKNOWN', reason: 'unknown', mastery_score: .1, due_at: null };
const options = { topic: '日常生活', difficulty: 'B1', length: 'short' };

test('practice settings require genuine targets and a bounded Unicode topic', () => {
  assert.equal(practiceProblem(options, [target]), '');
  assert.match(practiceProblem(options, []), /目标词/);
  assert.match(practiceProblem({ ...options, topic: '  ' }, [target]), /主题/);
  assert.equal(practiceProblem({ ...options, topic: `  ${'🌍'.repeat(100)}  ` }, [target]), '');
  assert.match(practiceProblem({ ...options, topic: '🌍'.repeat(101) }, [target]), /100/);
  assert.match(practiceProblem({ ...options, topic: 'city\nwalk' }, [target]), /控制字符/);
  assert.match(practiceProblem(options, [target, { ...target }]), /刷新/);
});

test('target reasons distinguish due known words from self-rated unfamiliar words', () => {
  assert.equal(practiceTargetReason(target), '目前不认识');
  assert.equal(practiceTargetReason({ ...target, reason: 'uncertain' }), '目前不确定');
  assert.equal(practiceTargetReason({ ...target, status: 'KNOWN', reason: 'due' }), '已到复习时间');
});

test('practice highlights use exact codepoint spans and preserve repeated words and literal markup', () => {
  const text = '🌍 中文 Learn. Learn. <script>read</script>\n';
  const matches = [{ vocabulary_id: 1, word: 'Learn', text_start: 5, text_end: 10 }, { vocabulary_id: 1, word: 'Learn', text_start: 12, text_end: 17 }];
  const snapshot = structuredClone(matches);
  const parts = practiceParts(text, matches, [target]);
  assert.equal(parts.map(part => part.text).join(''), text);
  assert.deepEqual(parts.filter(part => part.vocabularyId !== null).map(part => part.text), ['Learn', 'Learn']);
  assert.deepEqual(matches, snapshot);
  assert.equal(parts.at(-1).text, '. <script>read</script>\n');
});

test('invalid, foreign, fractional and mismatching target spans never alter or highlight source text', () => {
  const text = 'I learned today.';
  const matches = [
    { vocabulary_id: 2, word: 'learned', text_start: 2, text_end: 9 },
    { vocabulary_id: 1, word: 'learn', text_start: 2, text_end: 9 },
    { vocabulary_id: 1, word: 'learned', text_start: 2.5, text_end: 9 },
    { vocabulary_id: 1, word: 'learned', text_start: -1, text_end: 9 },
    { vocabulary_id: 1, word: 'learned', text_start: 2, text_end: 99 },
  ];
  assert.deepEqual(practiceParts(text, matches, [target]), [{ text, vocabularyId: null }]);
  assert.deepEqual(practiceParts(text, [], [target]), [{ text, vocabularyId: null }]);
});

test('overlapping word and phrase matches merge highlights while coverage retains both vocabulary IDs', () => {
  const targets = [target, { ...target, id: 2, word: 'training data', lemma: 'training data' }];
  const text = 'Our training data helps.';
  const matches = [
    { vocabulary_id: 1, word: 'data', text_start: 13, text_end: 17 },
    { vocabulary_id: 2, word: 'training data', text_start: 4, text_end: 17 },
  ];
  const snapshot = structuredClone(matches);
  const parts = practiceParts(text, matches, targets);
  assert.equal(parts.map(part => part.text).join(''), text);
  assert.deepEqual(parts.filter(part => part.vocabularyId !== null).map(part => part.text), ['training data']);
  assert.deepEqual(matches, snapshot);
  assert.equal(usablePractice({ ...options, source: 'llm', title: 'Practice', text, targets, matches }), true);
});

test('only actual AI previews covering every real target can be saved', () => {
  const result = { ...options, source: 'llm', title: 'A lesson', text: 'We learned.', targets: [target], matches: [{ vocabulary_id: 1, word: 'learned', text_start: 3, text_end: 10 }] };
  assert.equal(usablePractice(result), true);
  assert.equal(usablePractice(null), false);
  assert.equal(usablePractice({ ...result, source: 'unavailable' }), false);
  assert.equal(usablePractice({ ...result, title: ' ' }), false);
  assert.equal(usablePractice({ ...result, text: null }), false);
  assert.equal(usablePractice({ ...result, matches: [] }), false);
  assert.equal(usablePractice({ ...result, targets: [] }), false);
  assert.equal(usablePractice({ ...result, targets: [target, { ...target, id: 2 }] }), false);
});
