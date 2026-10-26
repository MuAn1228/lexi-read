import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/backupData.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { backupImportProblem, clearLearningPreferences, backupDate, backupSize, backupCheck, nextAutomaticBackup } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

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

test('restoration clears identity-scoped automatic reading continuation without touching another learner', () => {
  const values = new Map([['review', 'pending'], ['document', '1'], ['browsing', 'old-page'], ['lookup', 'old-queries'], ['reading', 'old-anchor'], ['other:reading', 'keep']]);
  const store = { removeItem: key => values.delete(key), getItem: key => values.get(key) ?? null };
  assert.equal(clearLearningPreferences(store, 'review', 'document', 'browsing', 'lookup', 'reading'), '');
  assert.deepEqual([...values], [['other:reading', 'keep']]);
  const ignored = { removeItem() {}, getItem: key => key === 'reading' ? 'retained' : null };
  assert.match(clearLearningPreferences(ignored, 'review', 'document', 'browsing', 'lookup', 'reading'), /自动阅读续接/);
});

test('automatic protection facts format sizes, checks and due states without inventing a backup', () => {
  assert.equal(backupSize(null), '尚无备份');
  assert.equal(backupSize(0), '0 B');
  assert.equal(backupSize(1024), '1 KB');
  assert.equal(backupSize(1536), '1.5 KB');
  assert.equal(backupSize(2 * 1024 * 1024), '2 MB');
  assert.equal(backupSize(-1), '大小不可用');
  assert.equal(backupCheck('ok'), '正常');
  assert.equal(backupCheck('unavailable'), '暂无法核验');
  assert.equal(backupCheck('failed'), '异常');

  const status = {
    database_ok: true, quick_check: 'ok', foreign_key_check: 'ok', last_success_at: null,
    last_size_bytes: null, backup_count: 0, next_due_at: null, due: true,
    last_attempt_at: null, error: null, created: false, retention: 7,
  };
  assert.equal(nextAutomaticBackup(status), '等待首次创建');
  assert.equal(nextAutomaticBackup({ ...status, last_success_at: '2026-10-25T00:00:00Z' }), '已到期，将在本机启动时补建');
  const future = { ...status, last_success_at: '2026-10-25T00:00:00Z', due: false, next_due_at: '2026-10-26T00:00:00Z' };
  assert.equal(nextAutomaticBackup(future), backupDate(future.next_due_at));
});
