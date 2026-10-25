import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/reviewAnnotationState.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { mergeReviewAnnotations, needsReviewAnnotations, needsReviewLesson } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const entry = { word: 'bank', lemma: 'bank', detail: null, cached: false, maimemo: null, can_fetch_annotations: true };

test('all cached Maimemo outcomes stop repeated automatic personal API reads', () => {
  assert.equal(needsReviewAnnotations(entry), true);
  assert.equal(needsReviewAnnotations({ ...entry, can_fetch_annotations: false }), false);
  for (const status of ['available', 'empty', 'unavailable', 'not_found']) {
    assert.equal(needsReviewAnnotations({ ...entry, maimemo: { status } }), false);
    assert.equal(needsReviewAnnotations({ ...entry, maimemo: { status, stale: true } }), true);
  }
});

test('late provider responses preserve the other source and its cached lesson flag', () => {
  const detail = { summary: '银行；河岸' }, maimemo = { status: 'available', interpretations: ['个人释义'] };
  const local = { ...entry, detail, cached: true };
  const merged = mergeReviewAnnotations(local, { ...entry, maimemo });
  assert.equal(merged.detail, detail); assert.equal(merged.cached, true); assert.equal(merged.maimemo, maimemo);
  const full = mergeReviewAnnotations(merged, { ...entry, detail, cached: false });
  assert.equal(full.maimemo, maimemo); assert.equal(full.cached, false);
  assert.equal(local.maimemo, null);
});

test('word identity changes never carry a previous word lesson or personal annotation', () => {
  const other = { ...entry, word: 'eye', lemma: 'eye' };
  assert.equal(mergeReviewAnnotations({ ...entry, detail: { summary: '银行' } }, other), other);
  assert.equal(mergeReviewAnnotations(null, entry), entry);
});

test('automatic detail fills missing instruction but respects personal definitions and examples', () => {
  const available = { ...entry, can_generate: true };
  assert.equal(needsReviewLesson(available), true);
  assert.equal(needsReviewLesson({ ...available, detail: {} }), false);
  assert.equal(needsReviewLesson({ ...available, can_generate: false }), false);
  assert.equal(needsReviewLesson({ ...available, maimemo: { interpretations: [{text:'n. 好奇心'}], phrases: [{english:'Stay curious.',chinese:'保持好奇。'}] } }), false);
  assert.equal(needsReviewLesson({ ...available, maimemo: { interpretations: [{text:'n. 好奇心'}], phrases: [] } }), true);
  assert.equal(needsReviewLesson({ ...available, maimemo: { interpretations: [{text:'Curiosity'}], phrases: [{english:'Stay curious.',chinese:''}] } }), true);
});
