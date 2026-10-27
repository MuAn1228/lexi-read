import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const options = { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } };
const learningSource = await readFile(new URL('../src/senseLearning.ts', import.meta.url), 'utf8');
const learningJs = ts.transpileModule(learningSource, options).outputText;
const learningUrl = `data:text/javascript;base64,${Buffer.from(learningJs).toString('base64')}`;
const storageSource = await readFile(new URL('../src/senseReviewStorage.ts', import.meta.url), 'utf8');
const storageJs = ts.transpileModule(storageSource, options).outputText
  .replace(/from ['"]\.\/senseLearning['"];/, `from '${learningUrl}';`);
const {
  finishSenseReviewCard, newSenseReviewSession, parseSenseReviewSession,
  readSenseReviewSession, reconcileSenseReviewSession, writeSenseReviewSession,
} = await import(`data:text/javascript;base64,${Buffer.from(storageJs).toString('base64')}`);

const timestamp = '2026-10-27T01:00:00Z';
const key = id => (id % 2 ? 'a' : 'b').repeat(64);
const makeItem = (senseId, meaning, status = 'UNKNOWN') => ({
  sense: {
    id: senseId, sense_key: key(senseId), source: 'ecdict', source_revision: '1'.repeat(64),
    source_ordinal: senseId - 1, part_of_speech: 'n.', meaning, status, mastery_score: 0.3,
    due_at: timestamp, known_streak: 0, available: true,
  },
  vocabulary: {
    id: 9, user_id: 1, word: 'bank', lemma: 'bank', status: null, mastery_score: null,
    seen_count: 2, lookup_count: 0, created_at: timestamp, updated_at: timestamp,
    first_seen_at: null, last_seen_at: timestamp,
  },
  last_reviewed_at: null, due_at: timestamp, known_streak: 0,
  next_intervals_seconds: { UNKNOWN: 600, UNCERTAIN: 86400, KNOWN: 259200 },
  priority: 80, plan_reason: 'FIRST_REVIEW', memory_state: 'FRAGILE', schedule_version: 'adaptive_v2',
});
const pending = item => ({
  item: structuredClone(item), status: 'KNOWN', requestId: '11111111-2222-4333-8444-555555555555',
  responseMs: 2300, planMinutes: 20,
});
const roundTrip = session => parseSenseReviewSession(JSON.stringify(session));

test('two meanings of the same vocabulary entry survive persistence as separate cards', () => {
  const session = newSenseReviewSession([makeItem(1, '银行'), makeItem(2, '河岸')]);
  assert.deepEqual(roundTrip(session), session);
  assert.deepEqual(session.items.map(item => item.sense.id), [1, 2]);
  const advanced = finishSenseReviewCard(session, 'assessed');
  assert.equal(advanced.cursor, 1);
  assert.equal(advanced.items[advanced.cursor].sense.meaning, '河岸');
});

test('a pending lost-response retry remains exact while other no-longer-due senses leave the group', () => {
  let session = finishSenseReviewCard(newSenseReviewSession([
    makeItem(1, '银行'), makeItem(2, '河岸'), makeItem(3, '储存处'),
  ]), 'assessed');
  session = { ...session, pending: pending(session.items[1]) };
  const reconciled = reconcileSenseReviewSession(session, [makeItem(3, '储存处')]);
  assert.deepEqual(reconciled.items.map(item => item.sense.id), [1, 2, 3]);
  assert.deepEqual(reconciled.pending, session.pending);
  assert.deepEqual(roundTrip(reconciled), reconciled);
});

test('tampered pending cards and malformed stable identities are rejected before a retry', () => {
  const session = newSenseReviewSession([makeItem(1, '银行'), makeItem(2, '河岸')]);
  session.pending = pending(session.items[0]);
  const mutate = change => { const copy = structuredClone(session); change(copy); return JSON.stringify(copy); };
  for (const raw of [
    '{', mutate(value => { value.items.push(value.items[0]); }),
    mutate(value => { value.items[0].sense.sense_key = 'ABC'; }),
    mutate(value => { value.items[0].sense.source_revision = null; }),
    mutate(value => { value.pending.item.vocabulary.word = 'shore'; }),
    mutate(value => { value.pending.item.sense.meaning = '被篡改'; }),
    mutate(value => { value.pending.requestId = 'bad-id'; }),
    mutate(value => { value.items[0].priority = 101; }),
  ]) assert.throws(() => parseSenseReviewSession(raw));
});

test('storage failures are explicit and never manufacture saved progress', () => {
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.match(readSenseReviewSession('key', blocked).warning, /存储不可用/);
  assert.throws(() => writeSenseReviewSession('key', newSenseReviewSession([]), blocked), /尚未发送这次自评/);
});
