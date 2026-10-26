import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/backupData.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { backupImportProblem, clearLearningPreferences, backupDate } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('backup selection accepts real-size ZIP boundaries and rejects empty or oversized archives', () => {
  assert.equal(backupImportProblem({ name: 'Lexi.ZIP', size: 250 * 1024 * 1024 }), '');
  assert.match(backupImportProblem({ name: 'database.sqlite3', size: 123 }), /ZIP/);
  assert.match(backupImportProblem({ name: 'backup.zip', size: 0 }), /空文件/);
  assert.match(backupImportProblem({ name: 'backup.zip', size: 250 * 1024 * 1024 + 1 }), /250 MB/);
});

test('restoration clears only current backend and identity preferences, preserving configuration and unrelated state', () => {
  const values = new Map([['scope:review', 'old-pending'], ['scope:document', '1'], ['other:review', 'unrelated'], ['config', 'keep']]);
  const storage = { removeItem: key => values.delete(key), getItem: key => values.get(key) ?? null };
  assert.equal(clearLearningPreferences(storage, 'scope:review', 'scope:document'), '');
  assert.deepEqual([...values], [['other:review', 'unrelated'], ['config', 'keep']]);
});

test('blocked or silently ignored storage cleanup reports failure and still attempts both preferences', () => {
  const attempted = [];
  const blocked = { removeItem(key) { attempted.push(key); if (key === 'review') throw new Error('blocked'); }, getItem: () => null };
  assert.match(clearLearningPreferences(blocked, 'review', 'document'), /未完成复习进度/);
  assert.deepEqual(attempted, ['review', 'document']);
  const ignored = { removeItem() {}, getItem: () => 'retained' };
  assert.match(clearLearningPreferences(ignored, 'review', 'document'), /未完成复习进度、上次阅读位置/);
  assert.equal(backupDate('invalid'), '时间不可用');
  assert.equal(backupDate('2026-10-18T02:30:00'), backupDate('2026-10-18T02:30:00Z'));
});

test('restoring a backup also clears only the current vocabulary browsing preferences', () => {
  const values = new Map([['review', 'pending'], ['document', '1'], ['browsing', 'old-page'], ['other:browsing', 'keep']]);
  const store = { removeItem: key => values.delete(key), getItem: key => values.get(key) ?? null };
  assert.equal(clearLearningPreferences(store, 'review', 'document', 'browsing'), '');
  assert.deepEqual([...values], [['other:browsing', 'keep']]);
  const ignored = { removeItem() {}, getItem: key => key === 'browsing' ? 'retained' : null };
  assert.match(clearLearningPreferences(ignored, 'review', 'document', 'browsing'), /词库浏览位置/);
});

test('restoration clears this identity lookup history and reports retained history without touching other scopes', () => {
  const values = new Map([['review', 'pending'], ['document', '1'], ['browsing', 'old-page'], ['lookup', 'old-queries'], ['other:lookup', 'keep']]);
  const store = { removeItem: key => values.delete(key), getItem: key => values.get(key) ?? null };
  assert.equal(clearLearningPreferences(store, 'review', 'document', 'browsing', 'lookup'), '');
  assert.deepEqual([...values], [['other:lookup', 'keep']]);
  const attempted = [];
  const blocked = { removeItem(key) { attempted.push(key); if (key === 'lookup') throw new Error('blocked'); }, getItem: () => null };
  assert.match(clearLearningPreferences(blocked, 'review', 'document', 'browsing', 'lookup'), /查词历史/);
  assert.deepEqual(attempted, ['review', 'document', 'browsing', 'lookup']);
});
