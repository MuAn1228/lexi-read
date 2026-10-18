import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/sentenceAnalysis.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { sentenceProblem } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('sentence limit counts original Unicode codepoints and accepts the exact 4,000-character boundary', () => {
  assert.equal(sentenceProblem('Read ' + '🌍'.repeat(3_995)), '');
  assert.match(sentenceProblem('Read ' + '🌍'.repeat(3_996)), /4,000/);
  assert.match(sentenceProblem('Read' + ' '.repeat(3_997)), /4,000/);
  assert.match(sentenceProblem('\n\t '), /没有可解析/);
  assert.equal(sentenceProblem('<img src=x onerror=alert(1)> Read & learn.'), '');
});
