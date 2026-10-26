import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/readingResume.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const resume = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const storage = () => {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), values };
};
const position = (documentId = 7, changes = {}) => ({
  documentId, contentIdentity: `identity-${documentId}`, selectedId: 72, surface: 'text', pageNumber: 3, zoom: 100,
  anchorId: 70, anchorOffset: -18.5, anchorLeftOffset: 2, fraction: 0.42, horizontalFraction: 0,
  updatedAt: 1_799_999_999_999, ...changes,
});

test('identity-scoped reading state round-trips the exact article, selection and viewport data', () => {
  const store = storage();
  let preferences = resume.emptyReadingResume();
  preferences = resume.rememberReadingPosition(preferences, position());
  assert.equal(resume.writeReadingResume('reader:mine', preferences, store), true);
  assert.deepEqual(resume.readReadingResume('reader:mine', store), { preferences, available: true });
  assert.deepEqual(resume.readReadingResume('reader:other', store), { preferences: resume.emptyReadingResume(), available: true });
  assert.deepEqual(resume.readingPositionForDocument(preferences, 7, 'identity-7'), position());
  assert.equal(resume.readingPositionForDocument(preferences, 7, 'changed-content'), null);
});

test('opening, updating and forgetting documents preserves bounded per-article positions', () => {
  let preferences = resume.emptyReadingResume();
  preferences = resume.rememberReadingDocument(preferences, 99);
  assert.equal(preferences.lastDocumentId, 99);
  for (let id = 1; id <= 35; id++) preferences = resume.rememberReadingPosition(preferences, position(id, { selectedId: null, anchorId: id }));
  assert.equal(preferences.lastDocumentId, 35);
  assert.equal(preferences.positions.length, 30);
  assert.deepEqual(preferences.positions.map(item => item.documentId).slice(0, 3), [35, 34, 33]);
  preferences = resume.rememberReadingPosition(preferences, position(20, { fraction: 0.75 }));
  assert.equal(preferences.positions[0].documentId, 20);
  assert.equal(preferences.positions[0].fraction, 0.75);
  assert.equal(preferences.positions.filter(item => item.documentId === 20).length, 1);
  preferences = resume.rememberReadingDocument(preferences, 35);
  preferences = resume.rememberReadingPosition(preferences, position(34, { fraction: 0.9 }), false);
  assert.equal(preferences.lastDocumentId, 35, 'a late flush updates the old article without stealing recent-article ownership');
  assert.equal(preferences.positions[0].documentId, 34);
  preferences = resume.forgetReadingDocument(preferences, 20);
  assert.equal(preferences.lastDocumentId, 35);
  assert.equal(preferences.positions.some(item => item.documentId === 20), false);
});

test('corrupt, oversized and out-of-range browser values cannot affect reading', () => {
  const store = storage();
  for (const raw of ['{', 'null', '[]', '{"version":2,"positions":[]}', ' '.repeat(50_001)]) {
    store.setItem('key', raw);
    assert.deepEqual(resume.readReadingResume('key', store), { preferences: resume.emptyReadingResume(), available: true });
  }
  const good = position();
  const invalid = [
    { ...good, documentId: 0 }, { ...good, selectedId: '72' }, { ...good, surface: 'canvas' },
    { ...good, pageNumber: 101 }, { ...good, zoom: 110 }, { ...good, anchorOffset: Infinity },
    { ...good, fraction: 1.1 }, { ...good, horizontalFraction: -0.1 }, { ...good, contentIdentity: '<bad\n>' },
  ];
  store.setItem('key', JSON.stringify({ version: 1, lastDocumentId: '7', positions: [invalid[0], good, ...invalid.slice(1), good] }));
  assert.deepEqual(resume.readReadingResume('key', store).preferences, { version: 1, lastDocumentId: null, positions: [good] });
});

test('denied or full browser storage remains an optional failure', () => {
  const blocked = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('full'); } };
  assert.deepEqual(resume.readReadingResume('key', blocked), { preferences: resume.emptyReadingResume(), available: false });
  assert.equal(resume.writeReadingResume('key', resume.emptyReadingResume(), blocked), false);
});

test('document identity ignores learning status but changes with text, occurrence IDs and PDF pages', () => {
  const base = {
    source: 'pdf', ocr_text: 'Read this page.', width: 1200, height: 1600,
    words: [{ id: 1, status: null }, { id: 2, status: null }], pages: [{ number: 1, text_start: 0, text_end: 15 }],
  };
  const identity = resume.readingDocumentIdentity(base);
  assert.equal(resume.readingDocumentIdentity({ ...base, words: [{ id: 1, status: 'KNOWN' }, { id: 2, status: 'UNKNOWN' }] }), identity);
  assert.notEqual(resume.readingDocumentIdentity({ ...base, ocr_text: 'Read another page.' }), identity);
  assert.notEqual(resume.readingDocumentIdentity({ ...base, words: [{ id: 11 }, { id: 12 }] }), identity);
  assert.notEqual(resume.readingDocumentIdentity({ ...base, pages: [{ number: 1, text_start: 0, text_end: 10 }] }), identity);
});
