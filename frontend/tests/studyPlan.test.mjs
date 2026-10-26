import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/studyPlan.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { DEFAULT_STUDY_PLAN_MINUTES, readStudyPlanPreferences, writeStudyPlanPreferences, normalizedStudyPlan,
  frozenResponseMs, newCardTimer, startCardTimer, pauseCardTimer, elapsedCardTimer } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

const summary = { total: 4, reviewed_today: 2, later_today: 3 };

test('budget preferences validate and stay isolated by caller supplied identity key', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  assert.equal(readStudyPlanPreferences('guest:a', storage).preferences.targetMinutes, DEFAULT_STUDY_PLAN_MINUTES);
  assert.equal(writeStudyPlanPreferences('guest:a', { version: 1, targetMinutes: 30 }, storage), true);
  assert.equal(readStudyPlanPreferences('guest:a', storage).preferences.targetMinutes, 30);
  assert.equal(readStudyPlanPreferences('guest:b', storage).preferences.targetMinutes, DEFAULT_STUDY_PLAN_MINUTES);
  values.set('guest:a', '{bad');
  assert.equal(readStudyPlanPreferences('guest:a', storage).preferences.targetMinutes, DEFAULT_STUDY_PLAN_MINUTES);
});

test('plan normalization clamps counts and calculates a safe progress fallback', () => {
  const plan = normalizedStudyPlan({ target_minutes: 20, estimated_seconds_per_card: 90, capacity_cards: 13, target_cards: 8,
    completed_cards: 3, remaining_cards: 5, available_now: 6, due_now: 8, later_today: 2, progress_percent: 37, status: 'READY' }, summary, 20);
  assert.equal(plan.target_cards, 8);
  assert.equal(plan.completed_cards, 3);
  assert.equal(plan.remaining_cards, 5);
  assert.equal(plan.available_now, 5);
  assert.equal(plan.progress_percent, 38);
  const damaged = normalizedStudyPlan({ target_cards: -4, completed_cards: -1, progress_percent: 500, status: 'UNKNOWN' }, summary, 10);
  assert.equal(damaged.target_cards, 0);
  assert.equal(damaged.progress_percent, 0);
  assert.equal(damaged.status, 'WAITING');
  const overTarget = normalizedStudyPlan({ target_minutes: 10, estimated_seconds_per_card: 35, capacity_cards: 17,
    target_cards: 17, completed_cards: 20, remaining_cards: 0, available_now: 0, due_now: 4,
    later_today: 0, progress_percent: 100, status: 'COMPLETE' }, summary, 10);
  assert.equal(overTarget.completed_cards, 20);
  assert.equal(overTarget.target_cards, 17);
  assert.equal(overTarget.progress_percent, 100);
});

test('timer pauses across hidden time and freezes a backend-valid response duration', () => {
  let timer = startCardTimer(newCardTimer(), 100);
  assert.equal(elapsedCardTimer(timer, 850), 750);
  timer = pauseCardTimer(timer, 850);
  assert.equal(elapsedCardTimer(timer, 10_000), 750);
  timer = startCardTimer(timer, 10_000);
  assert.equal(frozenResponseMs(elapsedCardTimer(timer, 10_249)), 999);
  assert.equal(frozenResponseMs(12), 500);
  assert.equal(frozenResponseMs(900_000), 600_000);
});
