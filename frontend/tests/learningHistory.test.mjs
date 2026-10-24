import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/learningHistory.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { learningHistoryPath, historyDateLabel, historyCalendarWeeks, groupHistoryEntries, historyActivityLevel,
  historyLatestLabel, historyPageNumbers } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const filters = { day: null, kind: 'all', q: '', page: 1 };
const day = date => ({ date, words: 0, reviews: 0, lookups: 0, marks: 0, actions: 0 });

test('history queries share range, local offset, filters and server pagination without injecting query syntax', () => {
  const value = learningHistoryPath(90, { day: '2026-10-24', kind: 'review', q: ' eye&kind=mark ', page: 5 }, 480);
  const url = new URL(value, 'https://example.test');
  assert.equal(url.pathname, '/learning-history');
  assert.deepEqual(Object.fromEntries(url.searchParams), { days: '90', tz_offset_minutes: '480', day: '2026-10-24',
    kind: 'review', q: 'eye&kind=mark', page: '5', page_size: '20' });
  assert.equal(url.searchParams.getAll('kind').length, 1);
  assert.equal(learningHistoryPath(7, filters, -420).includes('tz_offset_minutes=-420'), true);
});

test('cleared filters omit selected day and query, while search length matches the endpoint bound', () => {
  const empty = new URL(learningHistoryPath(30, filters, 0), 'https://example.test');
  assert.equal(empty.searchParams.has('day'), false);
  assert.equal(empty.searchParams.has('q'), false);
  const long = new URL(learningHistoryPath(30, { ...filters, q: 'x'.repeat(150) }, 0), 'https://example.test');
  assert.equal(long.searchParams.get('q').length, 100);
});

test('date-only captions preserve the selected calendar day in eastern and western device time zones', () => {
  const original = process.env.TZ;
  try {
    for (const tz of ['Asia/Shanghai', 'America/Los_Angeles', 'Pacific/Kiritimati']) {
      process.env.TZ = tz;
      assert.equal(historyDateLabel('2026-01-01'), '1月1日');
      assert.match(historyDateLabel('2026-01-01', true), /1月1日.*星期四/);
      assert.equal(historyDateLabel('2024-02-29'), '2月29日');
    }
  } finally { if (original === undefined) delete process.env.TZ; else process.env.TZ = original; }
});

test('Monday-first calendars preserve partial weeks and a Sunday month/year boundary', () => {
  const days = ['2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02', '2027-01-03', '2027-01-04'].map(day);
  const weeks = historyCalendarWeeks(days);
  assert.deepEqual(weeks.map(week => week.map(value => value?.date ?? null)), [
    [null, null, '2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02', '2027-01-03'],
    ['2027-01-04', null, null, null, null, null, null],
  ]);
  assert.deepEqual(weeks.flat().filter(Boolean), days);
  assert.deepEqual(historyCalendarWeeks([]), []);
});

test('7, 30 and 90 day calendars neither lose nor duplicate days across leap day and daylight saving changes', () => {
  for (const length of [7, 30, 90]) {
    const start = Date.UTC(2024, 1, 25);
    const days = Array.from({ length }, (_, index) => day(new Date(start + index * 86_400_000).toISOString().slice(0, 10)));
    const weeks = historyCalendarWeeks(days);
    assert.ok(weeks.every(week => week.length === 7));
    assert.deepEqual(weeks.flat().filter(Boolean), days);
    assert.equal(new Set(weeks.flat().filter(Boolean).map(value => value.date)).size, length);
  }
});

test('daily groups retain server order, repeated day entries, and unique keys across dates', () => {
  const entries = [
    { date: '2026-10-24', lemma: 'eye', key: '24:eye' },
    { date: '2026-10-23', lemma: 'eye', key: '23:eye' },
    { date: '2026-10-24', lemma: 'headmaster', key: '24:headmaster' },
  ];
  assert.deepEqual(groupHistoryEntries(entries), [
    { date: '2026-10-24', entries: [entries[0], entries[2]] },
    { date: '2026-10-23', entries: [entries[1]] },
  ]);
  assert.deepEqual(groupHistoryEntries([]), []);
});

test('activity colors describe action volume and never manufacture a score', () => {
  assert.deepEqual([0, 1, 2, 3, 5, 6, 10, 11, 500].map(historyActivityLevel), [0, 1, 1, 2, 2, 3, 3, 4, 4]);
  assert.equal(historyLatestLabel('MARK_KNOWN', true), '复习自评：已掌握');
  assert.equal(historyLatestLabel('MARK_UNKNOWN', false), '标记：不认识');
  assert.equal(historyLatestLabel('LOOKUP', false), '主动查看语境释义');
  assert.equal(historyLatestLabel('RECOMMENDATION_USEFUL', false), '认为关联有用');
  assert.equal(historyLatestLabel('NEW_UNRECOGNIZED_EVENT', false), '学习操作');
});

test('pagination remains bounded with both ends and a current-page neighborhood', () => {
  assert.deepEqual(historyPageNumbers(1, 1), [1]);
  assert.deepEqual(historyPageNumbers(2, 7), [1, 2, 3, 4, 5, 6, 7]);
  assert.deepEqual(historyPageNumbers(1, 50), [1, 2, 3, 4, 5, 'after', 50]);
  assert.deepEqual(historyPageNumbers(25, 50), [1, 'before', 24, 25, 26, 'after', 50]);
  assert.deepEqual(historyPageNumbers(50, 50), [1, 'before', 46, 47, 48, 49, 50]);
  for (let current = 1; current <= 100; current++) {
    const numbers = historyPageNumbers(current, 100).filter(value => typeof value === 'number');
    assert.equal(new Set(numbers).size, numbers.length);
    assert.ok(numbers.includes(1) && numbers.includes(100) && numbers.includes(current));
  }
});
