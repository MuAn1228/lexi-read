import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/senseLearning.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { senseCardKey, senseReviewPath, usableVocabularySenses, vocabularySensesPath } =
  await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const timestamp = '2026-10-27T01:00:00Z';
const sense = (key = 'a'.repeat(64), id = null) => ({
  id,
  sense_key: key,
  source: 'ecdict',
  source_revision: '1'.repeat(64),
  source_ordinal: 0,
  part_of_speech: 'n.',
  meaning: 'n. 银行；金融机构',
  status: id === null ? null : 'UNKNOWN',
  mastery_score: id === null ? null : 0.3,
  due_at: id === null ? null : timestamp,
  known_streak: 0,
  available: true,
});

test('sense endpoints encode the whole word and repeat only explicit restore ids', () => {
  assert.equal(vocabularySensesPath(' Bank '), '/vocabulary/Bank/senses');
  assert.equal(vocabularySensesPath('a/b?x=1', 'f'.repeat(64)),
    `/vocabulary/a%2Fb%3Fx%3D1/senses/${'f'.repeat(64)}`);
  const restored = new URL(senseReviewPath([7, 19], 20), 'http://localhost');
  assert.equal(restored.searchParams.get('limit'), '10');
  assert.equal(restored.searchParams.get('plan_minutes'), '20');
  assert.deepEqual(restored.searchParams.getAll('sense_ids'), ['7', '19']);
  assert.equal(new URL(senseReviewPath(undefined, 10), 'http://localhost').searchParams.has('sense_ids'), false);
});

test('catalog validation keeps complete stable senses and rejects partial or duplicate payloads', () => {
  const payload = { word: 'bank', lemma: 'bank', senses: [sense(), { ...sense('b'.repeat(64)), source_ordinal: 1 }] };
  assert.deepEqual(usableVocabularySenses(payload), payload);
  for (const mutate of [
    value => { value.senses[1].sense_key = value.senses[0].sense_key; },
    value => { value.senses[0].sense_key = 'ABC'; },
    value => { value.senses[0].source = 'remote'; },
    value => { value.senses[0].source_revision = null; },
    value => { value.senses[0].known_streak = 6; },
    value => { value.senses[0].meaning = ''; },
  ]) {
    const invalid = structuredClone(payload);
    mutate(invalid);
    assert.equal(usableVocabularySenses(invalid), null);
  }
});

test('card identity distinguishes two meanings of one word and prefers persisted sense ids', () => {
  assert.equal(senseCardKey(sense('a'.repeat(64), 7), 3), 'sense:7');
  assert.equal(senseCardKey(sense('b'.repeat(64), 8), 3), 'sense:8');
  assert.equal(senseCardKey(sense('c'.repeat(64)), 3), `word:3:sense-key:${'c'.repeat(64)}`);
});
