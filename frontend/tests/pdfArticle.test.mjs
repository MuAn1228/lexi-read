import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/pdfArticle.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { pdfImportProblem, pdfPageContent, pdfPageForOccurrence } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const word = (id, text, start, end) => ({ id, word: text, text_start: start, text_end: end, sentence: 'original context', status: 'UNKNOWN' });

test('PDF offsets preserve Unicode, empty pages, and repeated-word occurrence identity across pages', () => {
  const text = '🌍 Read.\n\n\n\nRead again.';
  const pages = [{ number: 1, text_start: 0, text_end: 7 }, { number: 2, text_start: 9, text_end: 9 }, { number: 3, text_start: 11, text_end: 22 }];
  const words = [word(1, 'Read', 2, 6), word(2, 'Read', 11, 15), word(3, 'again', 16, 21)];
  assert.equal(pdfPageForOccurrence({ pages, words }, 1), 1);
  assert.equal(pdfPageForOccurrence({ pages, words }, 2), 3);
  assert.equal(pdfPageForOccurrence({ pages, words }, null), null);
  assert.equal(pdfPageForOccurrence({ pages, words }, 99), null);
  assert.deepEqual(pdfPageContent(text, words, pages[0]), { text: '🌍 Read.', words: [words[0]] });
  assert.deepEqual(pdfPageContent(text, words, pages[1]), { text: '', words: [] });
  const third = pdfPageContent(text, words, pages[2]);
  assert.equal(third.text, 'Read again.');
  assert.deepEqual(third.words.map(item => [item.id, item.text_start, item.text_end]), [[2, 0, 4], [3, 5, 10]]);
  assert.equal(third.words[0].sentence, 'original context');
  assert.equal(third.words[0].status, 'UNKNOWN');
  assert.equal(words[1].text_start, 11);
});

test('page boundaries exclude crossing or invalid occurrences without losing page text', () => {
  const page = { number: 2, text_start: 6, text_end: 11 };
  const words = [word(1, 'abc', 4, 7), word(2, 'Read', 6, 10), word(3, '', 9, 9), word(4, 'bad', null, null), word(5, 'bad', 7.5, 9), word(6, 'bad', 10, 13)];
  const content = pdfPageContent('first\nRead!', words, page);
  assert.equal(content.text, 'Read!');
  assert.deepEqual(content.words.map(item => item.id), [2]);
  assert.equal(pdfPageForOccurrence({ pages: [page], words }, 1), null);
});

test('PDF validation accepts absent MIME but rejects wrong extension, wrong MIME, empty and oversized files', () => {
  assert.equal(pdfImportProblem({ name: 'English.PDF', type: '', size: 10 * 1024 * 1024 }), '');
  assert.equal(pdfImportProblem({ name: 'English.pdf', type: 'application/pdf', size: 500 }), '');
  assert.match(pdfImportProblem({ name: 'English.png', type: 'application/pdf', size: 500 }), /PDF 格式/);
  assert.match(pdfImportProblem({ name: 'English.pdf', type: 'image/png', size: 500 }), /PDF 格式/);
  assert.match(pdfImportProblem({ name: 'English.pdf', type: '', size: 0 }), /空文件/);
  assert.match(pdfImportProblem({ name: 'English.pdf', type: '', size: 10 * 1024 * 1024 + 1 }), /10 MB/);
});

test('all recognition modes share file limits; unsupported modes cannot silently start OCR', () => {
  const pdf = { name: 'English.pdf', type: 'application/pdf', size: 500 };
  for (const mode of ['auto', 'text', 'scan']) {
    assert.equal(pdfImportProblem(pdf, mode), '');
    assert.match(pdfImportProblem({ ...pdf, size: 10 * 1024 * 1024 + 1 }, mode), /10 MB/);
    assert.match(pdfImportProblem({ ...pdf, name: 'English.png' }, mode), /PDF 格式/);
  }
  for (const mode of ['', 'AUTO', 'scanned', 'scan ', null, true, 1, {}, ['scan']]) {
    assert.match(pdfImportProblem(pdf, mode), /识别方式/);
  }
  assert.equal(pdfImportProblem(pdf), '');
});
