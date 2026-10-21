import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/vocabularyCsv.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { vocabularyCsv } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const word = { word: 'model', lemma: 'model', status: 'UNKNOWN', mastery_score: null,
  seen_count: 0, lookup_count: 2, first_seen_at: null, last_seen_at: '2026-10-17T00:00:00Z' };

test('Chinese CSV has BOM, CRLF, actual counts and empty unassessed values', () => {
  const output = vocabularyCsv([word]);
  assert.ok(output.startsWith('\uFEFF"单词","词元"'));
  assert.ok(output.endsWith('"model","model","","","不认识","","0","2","","2026-10-17T00:00:00Z"\r\n'));
  assert.ok(vocabularyCsv([{ ...word, status: null, mastery_score: 0 }]).includes('"未标记","0","0"'));
});

test('CSV quotes commas, quotes and newlines without mutating input', () => {
  const entry = { ...word, word: 'a,"b"\nc' };
  assert.ok(vocabularyCsv([entry]).includes('"a,""b""\nc"'));
  assert.equal(entry.word, 'a,"b"\nc');
});

test('spreadsheet formula prefixes are exported as text', () => {
  for (const value of ['=SUM(1,1)', '+1', '-1', '@test', '\t=1', '  =1', '\nword']) {
    assert.ok(vocabularyCsv([{ ...word, word: value }]).split('\r\n')[1].startsWith('"\''));
  }
});

test('CSV includes actual Chinese dictionary summary and phonetic, safely quoted', () => {
  const entry = { ...word, meaning_summary: 'n. 模型，模范\nv. 塑造', phonetic: 'ˈmɒdl' };
  const csv = vocabularyCsv([entry]);
  assert.ok(csv.includes('"中文释义","音标"'));
  assert.ok(csv.includes('"n. 模型，模范\nv. 塑造","ˈmɒdl"'));
  assert.ok(vocabularyCsv([{ ...entry, meaning_summary: '=unsafe', phonetic: '@unsafe' }]).includes('"\'=unsafe","\'@unsafe"'));
  assert.equal(entry.meaning_summary, 'n. 模型，模范\nv. 塑造');
});
