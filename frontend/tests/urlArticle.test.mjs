import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/urlArticle.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { urlImportProblem, originalWebLink } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('web import accepts complete public URL syntax including Unicode paths, query strings and default ports', () => {
  for (const url of ['https://example.com/read?a=1&b=two#part', '  https://example.com/英文文章  ', 'http://example.com:80/read', 'https://example.com:443/read', 'HTTPS://EXAMPLE.COM/read']) assert.equal(urlImportProblem(url), '');
  assert.match(urlImportProblem(' '), /请先粘贴/);
  assert.match(urlImportProblem('https://example.com/' + 'a'.repeat(2048)), /2,048/);
});

test('web import rejects credentials, other schemes, ambiguous syntax, embedded whitespace and non-default ports', () => {
  for (const url of ['https://person:secret@example.com', 'https://person@example.com']) assert.match(urlImportProblem(url), /账号或密码/);
  for (const url of ['example.com/read', '//example.com/read', 'file:///read', 'javascript:alert(1)', 'data:text/html,hello', 'https:example.com']) assert.match(urlImportProblem(url), /http/);
  for (const url of ['https://example.com/a b', 'https://exam\nple.com', 'https://example.com/\u0000x', 'https://example.com\\evil']) assert.match(urlImportProblem(url), /空格、控制字符或反斜线/);
  assert.match(urlImportProblem('https://example.com:8000'), /默认端口/);
  assert.match(urlImportProblem('https://['), /有效/);
});

test('original links are safe metadata with normalized hostname and reject arbitrary executable schemes', () => {
  assert.deepEqual(originalWebLink('https://EXAMPLE.com/英文文章'), { href: 'https://example.com/%E8%8B%B1%E6%96%87%E6%96%87%E7%AB%A0', host: 'example.com' });
  for (const value of [null, undefined, '', 'javascript:alert(1)', 'https://user:pass@example.com', 'file:///etc/passwd', 'https://example.com/a\nb']) assert.equal(originalWebLink(value), null);
});
