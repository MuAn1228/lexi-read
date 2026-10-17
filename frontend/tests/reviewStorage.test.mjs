import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/reviewStorage.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { parseReviewSession, readReviewSession, writeReviewSession, newReviewSession, reconcileReviewSession, finishReviewCard, reviewQuery, intervalLabel } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const timestamp = '2026-10-17T01:00:00Z';
const makeItem = (id, status = 'UNKNOWN') => ({
  vocabulary: { id, user_id: 1, word: `word${id}`, lemma: `word${id}`, status, mastery_score: 0.5, seen_count: 2, lookup_count: 0,
    created_at: timestamp, updated_at: timestamp, first_seen_at: null, last_seen_at: timestamp },
  occurrence: { id: id * 10, document_id: 1, document_title: 'Reading', word: `word${id}`, sentence: 'A sentence.' },
  last_reviewed_at: null, due_at: timestamp, known_streak: 0, next_intervals_seconds: { UNKNOWN: 600, UNCERTAIN: 86400, KNOWN: 259200 },
});
const pending = item => ({ item: structuredClone(item), status: 'KNOWN', requestId: '11111111-2222-4333-8444-555555555555' });
const roundTrip = session => parseReviewSession(JSON.stringify(session));

test('uncompleted group round-trips with counts, known words, and an exact pending request snapshot', () => {
  const original = newReviewSession([makeItem(1), makeItem(2, 'KNOWN'), makeItem(3)]);
  const progress = finishReviewCard(original, 'assessed');
  progress.pending = pending(progress.items[1]);
  assert.deepEqual(roundTrip(progress), progress);
  assert.equal(progress.assessed, 1);
  assert.equal(progress.pending.item.vocabulary.status, 'KNOWN');
  assert.equal(original.cursor, 0);
});

test('server-committed pending request survives due filtering while other future cards leave the group', () => {
  let session = finishReviewCard(newReviewSession([makeItem(1), makeItem(2), makeItem(3), makeItem(4)]), 'assessed');
  session = { ...session, pending: pending(session.items[1]) };
  const dueKnown = makeItem(4, 'KNOWN');
  const reconciled = reconcileReviewSession(session, [dueKnown]);
  assert.deepEqual(reconciled.items.map(item => item.vocabulary.id), [1, 2, 4]);
  assert.deepEqual(reconciled.pending, session.pending);
  assert.equal(reconciled.pending.requestId, '11111111-2222-4333-8444-555555555555');
  assert.equal(reconciled.pending.item.occurrence.document_id, 1);
  assert.equal(reconciled.removed, 1);
  assert.equal(reconciled.assessed, 1);
  assert.equal(reconciled.items[2].vocabulary.status, 'KNOWN');
  assert.deepEqual(roundTrip(reconciled), reconciled);
});

test('confirmed request advances exactly one card and removes its pending retry state', () => {
  const session = newReviewSession([makeItem(1), makeItem(2)]);
  session.pending = pending(session.items[0]);
  const saved = finishReviewCard(session, 'assessed');
  assert.equal(saved.cursor, 1);
  assert.equal(saved.assessed, 1);
  assert.equal(saved.pending, null);
  assert.equal(session.cursor, 0);
  const completed = finishReviewCard(saved, 'skipped');
  assert.equal(completed.skipped, 1);
  assert.deepEqual(roundTrip(completed), completed);
  assert.deepEqual(finishReviewCard(completed, 'assessed'), completed);
});

test('putting down an unrecoverable card preserves earlier assessment counts and a valid session', () => {
  const session = finishReviewCard(newReviewSession([makeItem(1), makeItem(2), makeItem(3)]), 'assessed');
  session.pending = pending(session.items[1]);
  const dismissed = finishReviewCard(session, 'removed');
  assert.equal(dismissed.cursor, 1);
  assert.equal(dismissed.assessed, 1);
  assert.equal(dismissed.removed, 1);
  assert.equal(dismissed.pending, null);
  assert.equal(dismissed.items[1].vocabulary.id, 3);
  assert.deepEqual(roundTrip(dismissed), dismissed);
});

test('corrupt, obsolete, or inconsistent persisted state is rejected before retry can be sent', () => {
  const session = newReviewSession([makeItem(1), makeItem(2)]);
  session.pending = pending(session.items[0]);
  const mutate = change => { const copy = structuredClone(session); change(copy); return JSON.stringify(copy); };
  for (const raw of [
    '{', 'null', mutate(s => s.version = 2), mutate(s => s.cursor = 20), mutate(s => s.assessed = 1),
    mutate(s => s.items.push(s.items[0])), mutate(s => s.items[0].vocabulary.updated_at = 'broken date'),
    mutate(s => s.items[0].next_intervals_seconds.KNOWN = 0), mutate(s => s.pending.requestId = 'bad-id'),
    mutate(s => s.pending.item.vocabulary.id = 2), mutate(s => s.pending.item.occurrence.document_id = 3),
    mutate(s => s.pending.status = 'UNMARKED'), mutate(s => s.items[0].vocabulary.mastery_score = 10),
  ]) assert.throws(() => parseReviewSession(raw));
});

test('unreadable progress has an explicit warning without erasing server records', () => {
  const read = readReviewSession('key', { getItem: () => '{broken', setItem: () => assert.fail('read must not write') });
  assert.equal(read.session, null);
  assert.match(read.warning, /进度无法读取/);
  assert.match(read.warning, /学习记录仍在/);
});

test('blocked storage methods and a throwing localStorage getter are caught with actionable messages', () => {
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.match(readReviewSession('key', blocked).warning, /存储不可用/);
  assert.throws(() => writeReviewSession('key', newReviewSession([]), blocked), /尚未发送这次自评/);
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('SecurityError'); } });
  try {
    assert.match(readReviewSession('key').warning, /存储不可用/);
    assert.throws(() => writeReviewSession('key', newReviewSession([])), /尚未发送这次自评/);
  } finally {
    if (previous) Object.defineProperty(globalThis, 'localStorage', previous);
    else delete globalThis.localStorage;
  }
});

test('request query carries repeated vocabulary IDs and the current device timezone offset', () => {
  const query = new URL(reviewQuery([7, 19]), 'http://localhost').searchParams;
  assert.equal(query.get('limit'), '10');
  assert.equal(query.get('tz_offset_minutes'), String(-new Date().getTimezoneOffset()));
  assert.deepEqual(query.getAll('vocabulary_ids'), ['7', '19']);
  assert.equal(new URL(reviewQuery(), 'http://localhost').searchParams.has('vocabulary_ids'), false);
});

test('interval descriptions match the actual per-rating schedule supplied by the server', () => {
  assert.equal(intervalLabel(600), '10 分钟');
  assert.equal(intervalLabel(86400), '1 天');
  for (const days of [3, 7, 14, 30, 60]) assert.equal(intervalLabel(days * 86400), `${days} 天`);
});
