import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/mascot/mascotAssistant.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { assistantMessageProblem, assistantResponse, assistantHistory, assistantPayload, assistantQuickQuestion, assistantPanelPlacement } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const context = { key: 'reader:7:11', documentId: 7, occurrenceId: 11, word: 'curiosity', title: 'Reading', sentence: 'Curiosity helps us learn.' };
const response = { source: 'llm', answer: '它表示好奇心。', quiz: null, context_limited: false, warning: null };
const quiz = { question: 'Which word means 好奇心?', options: ['curiosity', 'courage', 'confidence', 'care'], answer_index: 0, explanation: 'curiosity 表示好奇心，其他三个词分别表示勇气、信心与关心。' };

test('questions count Unicode characters, preserve literal text and reject empty or invalid transport text', () => {
  assert.equal(assistantMessageProblem('  ' + '🌱'.repeat(500) + '\n'), '');
  assert.match(assistantMessageProblem('🌱'.repeat(501)), /500/);
  assert.match(assistantMessageProblem(' \n\t '), /告诉/);
  for (const value of ['why\u0000?', 'why\u0085?', '\ud800', '\udfff']) assert.match(assistantMessageProblem(value), /字符/);
  assert.equal(assistantMessageProblem('Explain\nthis\t<img src=x> literally.'), '');
});

test('reply validation separates a usable answer from an honest unavailable result', () => {
  assert.deepEqual(assistantResponse(response, 'chat'), response);
  const unavailable = { source: 'unavailable', answer: null, quiz: null, context_limited: false, warning: '请稍后重试。' };
  assert.deepEqual(assistantResponse(unavailable, 'chat'), unavailable);
  for (const value of [null, [], {}, { ...response, source: ['llm'] }, { ...response, answer: ' ' }, { ...response, context_limited: 'false' }, { ...response, warning: undefined }, { ...unavailable, answer: '假的回答' }]) assert.equal(assistantResponse(value, 'chat'), null);
});

test('a quiz needs four distinct options and a bounded integer answer, and cannot appear in a chat response', () => {
  const value = { ...response, answer: '来试一道选择题，选好后再看解析。', quiz };
  assert.deepEqual(assistantResponse(value, 'quiz'), value);
  assert.equal(assistantResponse(value, 'chat'), null);
  assert.equal(assistantResponse(response, 'quiz'), null);
  for (const invalid of [
    { ...quiz, options: quiz.options.slice(0, 3) }, { ...quiz, options: ['curiosity', ' CURIOSITY ', 'courage', 'care'] },
    { ...quiz, options: ['', ...quiz.options.slice(1)] }, { ...quiz, answer_index: '0' }, { ...quiz, answer_index: true },
    { ...quiz, answer_index: -1 }, { ...quiz, answer_index: 4 }, { ...quiz, answer_index: 1.1 }, { ...quiz, explanation: ' ' },
  ]) assert.equal(assistantResponse({ ...value, quiz: invalid }, 'quiz'), null);
});

test('follow-ups use only the last three successful replies without altering displayed turns', () => {
  const turns = Array.from({ length: 7 }, (_, id) => ({ id, question: `问题${id}`, mode: 'chat', response: { ...response, answer: `回答${id}` } }));
  turns[4].response = { source: 'unavailable', answer: null, quiz: null, context_limited: false, warning: null };
  turns[6].response.answer = ' ';
  const snapshot = structuredClone(turns);
  assert.deepEqual(assistantHistory(turns), [{ question: '问题2', answer: '回答2' }, { question: '问题3', answer: '回答3' }, { question: '问题5', answer: '回答5' }]);
  assert.deepEqual(turns, snapshot);
});

test('a hidden quiz contributes its question and options, revealing its answer only after explicit confirmation', () => {
  const turn = { id: 1, question: '出一道题', mode: 'quiz', response: { ...response, answer: '来试一道选择题，选好后再看解析。', quiz } };
  const hidden = assistantHistory([turn])[0].answer;
  assert.match(hidden, /Which word/); assert.match(hidden, /A\. curiosity/);
  assert.doesNotMatch(hidden, /(?:^|\n)(?:正确答案|解析|用户选择)：/);
  assert.ok(!hidden.includes(quiz.explanation));
  const revealed = assistantHistory([{ ...turn, quizChoice: 2 }])[0].answer;
  assert.match(revealed, /用户选择：C/); assert.match(revealed, /正确答案：A/); assert.match(revealed, /解析：curiosity/);
  const longQuiz = { ...quiz, options: quiz.options.map((value, i) => `${i}` + '🌱'.repeat(299)), explanation: '🌍'.repeat(799) };
  assert.ok(Array.from(assistantHistory([{ ...turn, quizChoice: 1, response: { ...turn.response, answer: '🌻'.repeat(2000), quiz: longQuiz } }])[0].answer).length <= 3000);
});

test('request context carries only server identities and a word, never client article text or display state', () => {
  const history = Array.from({ length: 5 }, (_, i) => ({ question: `Q${i}`, answer: `A${i}` }));
  const saved = structuredClone({ context, history });
  assert.deepEqual(assistantPayload(context, '  为什么？\n', 'explain', history), { message: '为什么？', mode: 'explain', document_id: 7, occurrence_id: 11, word: 'curiosity', history: history.slice(2) });
  assert.deepEqual({ context, history }, saved);
  const standalone = assistantPayload({ ...context, documentId: null }, 'Help', 'chat', []);
  assert.equal(standalone.document_id, null); assert.equal(standalone.occurrence_id, null);
  assert.equal(standalone.word, 'curiosity');
  assert.throws(() => assistantPayload(context, ' ', 'chat', []));
  assert.throws(() => assistantPayload({ ...context, word: 'x'.repeat(151) }, 'Help', 'chat', []));
});

test('quick actions are useful for a selected word, an article or independent English study', () => {
  for (const mode of ['explain', 'memory', 'quiz']) {
    assert.match(assistantQuickQuestion(mode, context), /curiosity/);
    assert.match(assistantQuickQuestion(mode, { ...context, word: null }), /文章/);
    assert.ok(assistantQuickQuestion(mode, { ...context, documentId: null, occurrenceId: null, word: null }).trim());
  }
});

test('panel placement preserves the mascot and stays inside desktop, phone and short landscape viewports', () => {
  for (const viewport of [{ x: 0, y: 0, width: 1440, height: 1000 }, { x: 0, y: 0, width: 390, height: 844 }, { x: 0, y: 20, width: 844, height: 370 }, { x: 14, y: 38, width: 340, height: 280 }]) {
    for (const side of ['left', 'right']) {
      const anchor = { x: side === 'left' ? viewport.x + 24 : viewport.x + viewport.width - 96, y: viewport.y + viewport.height - 110, width: 72, height: 85 };
      const saved = structuredClone({ anchor, viewport });
      const panel = assistantPanelPlacement(anchor, viewport);
      assert.ok(panel.x >= viewport.x + 12 && panel.x + panel.width <= viewport.x + viewport.width - 12);
      assert.ok(panel.y >= viewport.y + 12 && panel.y + panel.height <= viewport.y + viewport.height - 12);
      if (viewport.width >= 1000) assert.ok(side === 'right' ? panel.x + panel.width <= anchor.x - 12 : panel.x >= anchor.x + anchor.width + 12);
      assert.deepEqual({ anchor, viewport }, saved);
    }
  }
});
