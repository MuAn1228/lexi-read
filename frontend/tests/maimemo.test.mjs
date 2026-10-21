import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/maimemo.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { maimemoPreviewRemaining, maimemoImportProblem, maimemoWarning } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const now = Date.parse('2026-10-21T12:00:00Z');
const preview = overrides => ({
  preview_id: 'local-preview',
  expires_at: '2026-10-21T12:05:00',
  remote_total: 5,
  fetched_count: 5,
  studied_count: 3,
  planned_count: 2,
  valid_words: 4,
  invalid_words: 0,
  complete: true,
  warning: null,
  sample_words: ['apple', 'model', 'read', 'write'],
  ...overrides,
});
const chineseProblem = value => {
  assert.equal(typeof value, 'string');
  assert.match(value, /[\u4e00-\u9fff]/);
  assert.equal(value, value.trim());
};

test('preview countdown treats timezone-free server timestamps as UTC in a non-UTC locale', () => {
  const previousTimezone = process.env.TZ;
  process.env.TZ = 'Asia/Shanghai';
  try {
    assert.equal(maimemoPreviewRemaining(preview(), now), 300_000);
    assert.equal(maimemoPreviewRemaining(preview({ expires_at: '2026-10-21T12:05:00Z' }), now), 300_000);
    assert.equal(maimemoPreviewRemaining(preview({ expires_at: '2026-10-21T20:05:00+08:00' }), now), 300_000);
    assert.equal(maimemoPreviewRemaining(preview({ expires_at: '2026-10-21T12:05:00.123456' }), now), 300_123);
  } finally {
    if (previousTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = previousTimezone;
  }
});

test('countdown preserves milliseconds and expires exactly at the boundary', () => {
  const value = preview();
  const expiry = Date.parse('2026-10-21T12:05:00Z');
  assert.equal(maimemoPreviewRemaining(value, expiry - 1001), 1001);
  assert.equal(maimemoPreviewRemaining(value, expiry - 1000), 1000);
  assert.equal(maimemoPreviewRemaining(value, expiry - 1), 1);
  assert.equal(maimemoPreviewRemaining(value, expiry), 0);
  assert.equal(maimemoPreviewRemaining(value, expiry + 1), 0);
});

test('missing previews and unreadable expiry timestamps have no remaining time', () => {
  assert.equal(maimemoPreviewRemaining(null, now), 0);
  assert.equal(maimemoPreviewRemaining(preview({ expires_at: '' }), now), 0);
  assert.equal(maimemoPreviewRemaining(preview({ expires_at: 'not-a-date' }), now), 0);
});

test('import requires a preview that is still valid at confirmation time', () => {
  const missing = maimemoImportProblem(null, false, now);
  chineseProblem(missing);
  assert.match(missing, /预览|读取/);
  const expiry = Date.parse('2026-10-21T12:05:00Z');
  assert.equal(maimemoImportProblem(preview(), false, expiry - 1), '');
  for (const timestamp of [expiry, expiry + 1]) {
    const expired = maimemoImportProblem(preview(), true, timestamp);
    chineseProblem(expired);
    assert.match(expired, /过期|重新读取/);
  }
  chineseProblem(maimemoImportProblem(preview({ expires_at: 'not-a-date' }), true, now));
});

test('an empty normalized vocabulary cannot be imported despite positive raw record counts', () => {
  for (const valid_words of [0, -1]) {
    for (const includePlanned of [false, true]) {
      const problem = maimemoImportProblem(preview({ valid_words }), includePlanned, now);
      chineseProblem(problem);
      assert.match(problem, /有效|词汇|单词/);
    }
  }
});

test('studied-only import needs studied records while planned words can be explicitly included', () => {
  for (const studied_count of [0, -1]) {
    const value = preview({ studied_count, planned_count: 5 });
    const problem = maimemoImportProblem(value, false, now);
    chineseProblem(problem);
    assert.match(problem, /学习|学过|已学/);
    assert.equal(maimemoImportProblem(value, true, now), '');
  }
  assert.equal(maimemoImportProblem(preview({ studied_count: 1, planned_count: 0 }), false, now), '');
  assert.equal(maimemoImportProblem(preview({ studied_count: 1, planned_count: 0 }), true, now), '');
});

test('multiple raw records for one normalized word remain importable in both scopes', () => {
  // save_preview counts studied/planned raw records separately from unique valid_words.
  const value = preview({
    fetched_count: 5,
    studied_count: 3,
    planned_count: 2,
    valid_words: 1,
    sample_words: ['models'],
  });
  assert.equal(maimemoImportProblem(value, false, now), '');
  assert.equal(maimemoImportProblem(value, true, now), '');
});

test('partial snapshots with usable words are importable and retain the service warning', () => {
  const value = preview({
    remote_total: 100,
    complete: false,
    warning: '  接口提前结束，仅获取部分记录。 \n',
  });
  assert.equal(maimemoImportProblem(value, false, now), '');
  assert.equal(maimemoImportProblem(value, true, now), '');
  const warning = maimemoWarning(value);
  assert.equal(warning, warning.trim());
  assert.ok(warning.includes('接口提前结束，仅获取部分记录。'));
});

test('complete snapshots display trimmed warnings only when supplied', () => {
  assert.equal(maimemoWarning({ complete: true, warning: null }), '');
  assert.equal(maimemoWarning({ complete: true, warning: '' }), '');
  assert.equal(maimemoWarning({ complete: true, warning: ' \n\t ' }), '');
  assert.equal(maimemoWarning({ complete: true, warning: '  服务提示 \n' }), '服务提示');
});

test('incomplete snapshots always explain partial fetching even without a service warning', () => {
  for (const warning of [null, '', ' \n\t ']) {
    const message = maimemoWarning({ complete: false, warning });
    chineseProblem(message);
    assert.match(message, /部分|未完整|不完整|未能完整|尚未完整/);
  }
});
