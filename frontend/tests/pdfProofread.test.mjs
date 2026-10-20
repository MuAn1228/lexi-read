import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/pdfProofread.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { normalizeProofreadText, proofreadChangedPages, proofreadCharacterCount, proofreadClipboardText, proofreadProblem, proofreadPayload } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const page = (number, text) => ({ number, text });

test('proofreading normalizes CR and CRLF without removing intentional whitespace or Unicode', () => {
  assert.equal(normalizeProofreadText('  🌍 Read.\r\n\tNext\rLast\n '), '  🌍 Read.\n\tNext\nLast\n ');
});

test('proofreading character limits count Unicode codepoints and every original page separator', () => {
  assert.equal(proofreadCharacterCount([page(1, '🌍 A\r\nB'), page(2, ''), page(3, '词')] ), 10);
  assert.equal(proofreadCharacterCount([]), 0);
  assert.equal(proofreadCharacterCount([page(1, '')]), 0);
  assert.equal(proofreadCharacterCount([page(1, ''), page(2, ''), page(3, '')]), 4);
});

test('proofreading changes distinguish repeated page content and a page restored to its original text', () => {
  const original = [page(1, 'Read.\nNext.'), page(2, ''), page(3, 'Read.\nNext.')];
  const draft = [page(1, 'Read.\r\nNext.'), page(2, 'New English.'), page(3, 'Corrected.')];
  assert.deepEqual(proofreadChangedPages(original, draft), [2, 3]);
  draft[2].text = original[2].text;
  assert.deepEqual(proofreadChangedPages(original, draft), [2]);
  assert.deepEqual(original[1], page(2, ''));
});

test('proofreading accepts corrections on blank pages and preserves blank pages elsewhere', () => {
  const original = [page(1, 'Read.'), page(2, ''), page(3, 'Last.')];
  assert.equal(proofreadProblem(original, [page(1, ''), page(2, '🌍 New\tEnglish.'), page(3, '')]), '');
});

test('proofreading cannot add, delete, reorder, duplicate or relabel original pages', () => {
  const original = [page(1, 'First.'), page(2, ''), page(3, 'Last.')];
  for (const draft of [original.slice(0, 2), [...original, page(4, '')], [original[1], original[0], original[2]], [original[0], original[0], original[2]], [original[0], page(20, ''), original[2]]]) {
    assert.match(proofreadProblem(original, draft), /页码/);
  }
});

test('proofreading enforces whole-document limit at the exact Unicode and separator boundary', () => {
  const original = [page(1, 'A'), page(2, '')];
  assert.equal(proofreadProblem(original, [page(1, `A${'🌍'.repeat(7)}`), page(2, '')], 10), '');
  assert.match(proofreadProblem(original, [page(1, `A${'🌍'.repeat(8)}`), page(2, '')], 10), /最多支持 10 字/);
  assert.equal(proofreadProblem([page(1, '')], [page(1, 'A'.repeat(200_000))]), '');
  assert.match(proofreadProblem(original, [page(1, 'A'.repeat(199_999)), page(2, '')]), /200,000/);
});

test('proofreading rejects C0, C1 and lone-surrogate controls but accepts emoji, newline and tab', () => {
  const original = [page(1, 'Read.')];
  for (const control of ['\u0000', '\u0008', '\u000b', '\u000c', '\u001b', '\u007f', '\u0085', '\u009f', '\ud800', '\udfff']) {
    assert.match(proofreadProblem(original, [page(1, `Read ${control}.`)]), /控制字符/);
  }
  assert.equal(proofreadProblem(original, [page(1, '🌍 Read.\r\n\tAgain.\r')]), '');
});

test('proofreading requires English content somewhere while allowing individual empty pages', () => {
  const original = [page(1, 'Read.'), page(2, '')];
  for (const text of ['', '  \n\t ', '中文。🌍123!?']) {
    assert.match(proofreadProblem(original, [page(1, text), page(2, '')]), /英文单词/);
  }
  assert.equal(proofreadProblem(original, [page(1, ''), page(2, 'English.')]), '');
});

test('proofreading retry payload snapshots all pages, request UUID and source revision', () => {
  const original = [page(1, 'Read.'), page(2, ''), page(3, 'Last.')];
  const document = { document_id: 17, revision: 'a'.repeat(64), max_characters: 200_000, pages: original };
  const draft = [page(1, '🌍 Corrected.\r\nNext.'), page(2, ''), page(3, 'Last.')];
  const payload = proofreadPayload(document, draft, '00000000-0000-4000-8000-000000000001');
  const encoded = JSON.stringify(payload);
  draft[0].text = 'Subsequent edit.'; draft.push(page(4, 'Added.')); document.revision = 'b'.repeat(64);
  assert.equal(JSON.stringify(payload), encoded);
  assert.deepEqual(payload, { request_id: '00000000-0000-4000-8000-000000000001', base_revision: 'a'.repeat(64), pages: [page(1, '🌍 Corrected.\nNext.'), page(2, ''), page(3, 'Last.')] });
  assert.equal(original[0].text, 'Read.');
});

test('image proofreading copies exact text without exposing the internal virtual page', () => {
  const text = '🌍 Read, read!\n\n  Keep\tspacing.\n';
  assert.equal(proofreadClipboardText('image', [page(1, text)]), text);
  assert.equal(proofreadClipboardText('pdf', [page(1, text), page(2, '')]), `--- 第 1 页 ---\n${text}\n\n--- 第 2 页 ---\n`);
});

test('image proofreading validates one exact segment and uses image-specific guidance', () => {
  const original = [page(1, 'OCR mistake.')];
  assert.equal(proofreadProblem(original, [page(1, '🌍 Corrected.\r\n\tRead again.')], 200_000, 'image'), '');
  assert.equal(proofreadProblem(original, [page(1, '中文。🌍123!?')], 200_000, 'image'), '请保留至少一个英文单词。');
  for (const draft of [[], [page(2, 'Read.')], [page(1, 'Read.'), page(2, 'More.')]]) {
    assert.match(proofreadProblem(original, draft, 200_000, 'image'), /正文结构/);
  }
  assert.match(proofreadProblem(original, [page(1, `A${'🌍'.repeat(200_000)}`)], 200_000, 'image'), /200,000/);
});
