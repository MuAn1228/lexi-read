import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/sentenceAnalysis.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { sentenceProblem, sentenceCacheKey, sentenceOccurrenceKey, rememberSentenceAnalysis, SENTENCE_CACHE_LIMIT } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('sentence limit counts original Unicode codepoints and accepts the exact 4,000-character boundary', () => {
  assert.equal(sentenceProblem('Read ' + '🌍'.repeat(3_995)), '');
  assert.match(sentenceProblem('Read ' + '🌍'.repeat(3_996)), /4,000/);
  assert.match(sentenceProblem('Read' + ' '.repeat(3_997)), /4,000/);
  assert.match(sentenceProblem('\n\t '), /没有可解析/);
  assert.equal(sentenceProblem('<img src=x onerror=alert(1)> Read & learn.'), '');
});

test('sentence memory accepts matching successful analyses, isolates page starts and stays bounded', () => {
  const cache = new Map();
  const sentence = 'A model helps us learn.';
  const key = sentenceCacheKey(0, sentence);
  const valid = { sentence, source: 'llm', translation: '模型帮助我们学习。' };
  rememberSentenceAnalysis(cache, key, sentence, { ...valid, source: 'unavailable' });
  rememberSentenceAnalysis(cache, key, sentence, { ...valid, sentence: 'Another sentence.' });
  assert.equal(cache.size, 0);
  rememberSentenceAnalysis(cache, key, sentence, valid);
  assert.equal(cache.get(key), valid);
  assert.notEqual(key, sentenceCacheKey(42, sentence));
  for (let i = 0; i < SENTENCE_CACHE_LIMIT; i++) {
    const text = `Sentence ${i}.`;
    rememberSentenceAnalysis(cache, sentenceCacheKey(0, text), text, { ...valid, sentence: text });
  }
  assert.equal(cache.size, SENTENCE_CACHE_LIMIT);
  assert.equal(cache.has(key), false);
});

test('same occurrence shares across words, repeated sentences and unreliable spans stay separate', () => {
  const text = '🌱 Before. A model helps.\nNext. A model helps.';
  const chars = Array.from(text);
  const first = chars.join('').indexOf('A model');
  const start = Array.from(text.slice(0, first)).length;
  const word = { id: 1, word: 'model', sentence: 'A model helps.', text_start: start + 2, text_end: start + 7 };
  const other = { ...word, id: 2, word: 'helps', text_start: start + 8, text_end: start + 13 };
  assert.equal(sentenceOccurrenceKey(text, word), sentenceOccurrenceKey(text, other));
  const repeatedStart = chars.length - Array.from('A model helps.').length;
  const repeated = { ...word, id: 3, text_start: repeatedStart + 2, text_end: repeatedStart + 7 };
  assert.notEqual(sentenceOccurrenceKey(text, word), sentenceOccurrenceKey(text, repeated));
  const raw = '🌱 A  model\nhelps.';
  assert.equal(sentenceOccurrenceKey(raw, { ...word, text_start: 5, text_end: 10, sentence: '🌱 A model helps.' }), sentenceOccurrenceKey(raw, { ...other, text_start: 11, text_end: 16, sentence: '🌱 A model helps.' }));
  assert.notEqual(sentenceOccurrenceKey(text, { ...word, text_start: null }), sentenceOccurrenceKey(text, { ...other, text_start: null }));
});
