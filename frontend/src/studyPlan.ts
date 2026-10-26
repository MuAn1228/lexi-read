import type { MemoryState, PlanReason, ReviewSummary, StudyPlan, StudyPlanMinutes } from './types';

export interface StudyPlanPreferences { version: 1; targetMinutes: StudyPlanMinutes }
type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;

export const STUDY_PLAN_MINUTES: readonly StudyPlanMinutes[] = [10, 20, 30];
export const DEFAULT_STUDY_PLAN_MINUTES: StudyPlanMinutes = 20;
const validMinutes = (value: unknown): value is StudyPlanMinutes => STUDY_PLAN_MINUTES.includes(value as StudyPlanMinutes);
const finite = (value: unknown, fallback = 0) => typeof value === 'number' && Number.isFinite(value) ? value : fallback;
const count = (value: unknown, fallback = 0) => Math.max(0, Math.round(finite(value, fallback)));

export function readStudyPlanPreferences(key: string, storage?: StorageLike): { preferences: StudyPlanPreferences; available: boolean } {
  const fallback = { preferences: { version: 1 as const, targetMinutes: DEFAULT_STUDY_PLAN_MINUTES }, available: true };
  try {
    const raw = (storage ?? localStorage).getItem(key);
    if (!raw || raw.length > 2_000) return fallback;
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return fallback;
    const saved = value as Record<string, unknown>;
    if (saved.version !== 1 || !validMinutes(saved.targetMinutes)) return fallback;
    return { preferences: { version: 1, targetMinutes: saved.targetMinutes }, available: true };
  } catch { return { ...fallback, available: false }; }
}

export function writeStudyPlanPreferences(key: string, preferences: StudyPlanPreferences, storage?: StorageLike): boolean {
  if (!key || !validMinutes(preferences.targetMinutes)) return false;
  try { (storage ?? localStorage).setItem(key, JSON.stringify({ version: 1, targetMinutes: preferences.targetMinutes })); return true; }
  catch { return false; }
}

/** Keep the plan usable while an older backend or a damaged response is being upgraded. */
export function normalizedStudyPlan(plan: StudyPlan | undefined, summary: ReviewSummary, requested: StudyPlanMinutes): StudyPlan {
  const seconds = Math.max(15, Math.min(120, count(plan?.estimated_seconds_per_card, 60)));
  const capacity = Math.max(1, count(plan?.capacity_cards, Math.floor(requested * 60 / seconds)));
  const due = count(plan?.due_now, summary.total);
  const later = count(plan?.later_today, summary.later_today);
  const completed = count(plan?.completed_cards, summary.reviewed_today);
  const target = Math.min(capacity, Math.max(completed, count(plan?.target_cards, Math.min(capacity, completed + due + later))));
  const remaining = Math.max(0, Math.min(Math.max(0, target - completed), count(plan?.remaining_cards, target - completed)));
  const available = Math.min(due, remaining, count(plan?.available_now, Math.min(due, remaining)));
  const progress = target > 0 ? Math.min(100, Math.round(completed / target * 100)) : 0;
  const status = plan?.status && ['READY', 'WAITING', 'COMPLETE', 'LIGHT'].includes(plan.status)
    ? plan.status : target === 0 ? 'WAITING' : remaining === 0 ? 'COMPLETE' : target < capacity ? 'LIGHT' : 'READY';
  return {
    target_minutes: validMinutes(plan?.target_minutes) ? plan!.target_minutes : requested,
    estimated_seconds_per_card: seconds,
    capacity_cards: capacity,
    target_cards: target,
    // Keep the factual count even when the learner lowers today's budget
    // below work they have already completed. Only the progress width clamps.
    completed_cards: completed,
    remaining_cards: remaining,
    available_now: available,
    due_now: due,
    later_today: later,
    // The visible progress bar is card based, so its width always matches the
    // completed/target numbers announced to assistive technology.
    progress_percent: progress,
    status,
  };
}

export const planReasonLabel = (reason?: PlanReason): string => reason ? ({
  FIRST_REVIEW: '首次进入复习', OVERDUE: '已经逾期', RECENT_LAPSE: '最近忘记过',
  REPEATED_DIFFICULTY: '多次觉得困难', FREQUENT_IN_READING: '阅读中经常遇见', DUE: '按计划到期',
} as Partial<Record<PlanReason, string>>)[reason] ?? '按当前计划安排' : '按当前计划安排';

export const memoryStateLabel = (state?: MemoryState): string => state ? ({
  FRAGILE: '需要巩固', BUILDING: '正在稳定', STABLE: '较为稳定',
} as Partial<Record<MemoryState, string>>)[state] ?? '等待本次自评' : '等待本次自评';

export interface CardTimer { elapsed: number; startedAt: number | null }
export const newCardTimer = (): CardTimer => ({ elapsed: 0, startedAt: null });
export function startCardTimer(timer: CardTimer, now: number): CardTimer {
  return timer.startedAt === null ? { ...timer, startedAt: now } : timer;
}
export function pauseCardTimer(timer: CardTimer, now: number): CardTimer {
  return timer.startedAt === null ? timer : { elapsed: timer.elapsed + Math.max(0, now - timer.startedAt), startedAt: null };
}
export function elapsedCardTimer(timer: CardTimer, now: number): number {
  return timer.elapsed + (timer.startedAt === null ? 0 : Math.max(0, now - timer.startedAt));
}
/** Match the backend assessment schema and freeze this value into the retry record. */
export function frozenResponseMs(elapsed: number): number {
  return Math.max(500, Math.min(600_000, Math.round(Number.isFinite(elapsed) ? elapsed : 0)));
}
