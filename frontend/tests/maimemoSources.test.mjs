import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';
const source = await readFile(new URL('../src/maimemoSources.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { maimemoResponse, representativeRecord, maimemoWordList, matchesMaimemoSource } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
test('export to Maimemo includes only self-assessed difficult words, sorted and deduplicated', () => {
  const words = [{lemma:'model',status:'UNKNOWN'}, {lemma:'apple',status:'UNCERTAIN'}, {lemma:'model',status:'UNKNOWN'}, {lemma:'planned',status:null}, {lemma:'known',status:'KNOWN'}];
  assert.equal(maimemoWordList(words), 'apple\r\nmodel');
  assert.equal(maimemoWordList([]), '');
});
test('remote source representative keeps records separate and explains actual feedback', () => {
  const source = {records:[{voc_spelling:'models',study_count:3},{voc_spelling:'model',study_count:7}]};
  assert.equal(representativeRecord(source).study_count, 7);
  assert.equal(source.records.length, 2);
  assert.equal(representativeRecord(undefined), undefined);
  assert.equal(maimemoResponse('FORGET'), '忘记');
  assert.equal(maimemoResponse('arbitrary unknown'), '暂无反馈');
});
test('Maimemo weakness filter uses imported feedback or sticky tag without inventing local mastery', () => {
  assert.equal(matchesMaimemoSource(undefined,'ALL'),true);
  assert.equal(matchesMaimemoSource(undefined,'MAIMEMO'),false);
  for (const record of [{last_response:'FORGET',tags:[]},{last_response:'VAGUE',tags:[]},{last_response:'FAMILIAR',tags:['STICKING']}]) assert.equal(matchesMaimemoSource({records:[record]},'MAIMEMO_WEAK'),true);
  assert.equal(matchesMaimemoSource({records:[{last_response:'WELL_FAMILIAR',tags:[]}]},'MAIMEMO_WEAK'),false);
});
test('valid English lemma constructor cannot turn a missing source into an inherited object crash', () => {
  const sources = {};
  assert.equal(representativeRecord(sources.constructor),undefined);
  assert.equal(matchesMaimemoSource(sources.constructor,'MAIMEMO_WEAK'),false);
  assert.equal(representativeRecord({records:null}),undefined);
});
