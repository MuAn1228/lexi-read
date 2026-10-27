import { senseCardKey } from './senseLearning';
import type { SenseReviewItem, StudyPlanMinutes, WordStatus } from './types';

export interface PendingSenseReview {
  item: SenseReviewItem;
  status: WordStatus;
  requestId: string;
  responseMs?: number;
  planMinutes?: StudyPlanMinutes;
}

export interface SenseReviewSession {
  version: 1;
  items: SenseReviewItem[];
  cursor: number;
  size: number;
  assessed: number;
  skipped: number;
  removed: number;
  pending: PendingSenseReview | null;
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;
const statuses = new Set(['KNOWN', 'UNKNOWN', 'UNCERTAIN']);
const reasons = new Set(['FIRST_REVIEW', 'OVERDUE', 'RECENT_LAPSE', 'REPEATED_DIFFICULTY', 'FREQUENT_IN_READING', 'DUE']);
const memories = new Set(['FRAGILE', 'BUILDING', 'STABLE']);
const allowedMinutes = new Set([10, 20, 30]);
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
const counter = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = (value: unknown): value is string => typeof value === 'string';
const date = (value: unknown): value is string => text(value) && Number.isFinite(Date.parse(/[zZ]$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`));
const nullableDate = (value: unknown) => value === null || date(value);

function validItem(value: unknown): value is SenseReviewItem {
  if (!record(value) || !record(value.sense) || !record(value.vocabulary) || !record(value.next_intervals_seconds)) return false;
  const sense = value.sense;
  const word = value.vocabulary;
  return positive(sense.id) && text(sense.sense_key) && /^[0-9a-f]{64}$/.test(sense.sense_key)
    && (sense.source === 'ecdict' || sense.source === 'lexi_supplement')
    && text(sense.source_revision) && /^[0-9a-f]{64}$/.test(sense.source_revision)
    && counter(sense.source_ordinal) && sense.source_ordinal <= 1000
    && (sense.part_of_speech === null || (text(sense.part_of_speech) && sense.part_of_speech.length > 0 && sense.part_of_speech.length <= 80))
    && text(sense.meaning) && Boolean(sense.meaning.trim()) && sense.meaning.length <= 10_000 && statuses.has(String(sense.status))
    && (sense.mastery_score === null || (typeof sense.mastery_score === 'number' && Number.isFinite(sense.mastery_score) && sense.mastery_score >= 0 && sense.mastery_score <= 1))
    && date(sense.due_at) && counter(sense.known_streak) && sense.known_streak <= 5 && typeof sense.available === 'boolean'
    && positive(word.id) && positive(word.user_id) && text(word.word) && Boolean(word.word) && text(word.lemma) && Boolean(word.lemma)
    && (word.status === null || statuses.has(String(word.status)))
    && (word.mastery_score === null || (typeof word.mastery_score === 'number' && Number.isFinite(word.mastery_score) && word.mastery_score >= 0 && word.mastery_score <= 1))
    && counter(word.seen_count) && counter(word.lookup_count) && date(word.created_at) && date(word.updated_at)
    && nullableDate(word.first_seen_at) && nullableDate(word.last_seen_at)
    && nullableDate(value.last_reviewed_at) && date(value.due_at) && counter(value.known_streak) && value.known_streak <= 5
    && reasons.has(String(value.plan_reason)) && memories.has(String(value.memory_state))
    && typeof value.priority === 'number' && Number.isFinite(value.priority) && value.priority >= 0 && value.priority <= 100
    && value.schedule_version === 'adaptive_v2'
    && [...statuses].every(status => positive((value.next_intervals_seconds as Record<string, unknown>)[status]));
}

export function parseSenseReviewSession(raw: string): SenseReviewSession {
  const value: unknown = JSON.parse(raw);
  if (!record(value) || value.version !== 1 || !Array.isArray(value.items) || value.items.length > 10
    || !value.items.every(validItem) || !counter(value.cursor) || !counter(value.size) || value.size > 10
    || !counter(value.assessed) || !counter(value.skipped) || !counter(value.removed)
    || value.cursor > value.items.length || value.assessed + value.skipped !== value.cursor
    || value.items.length + value.removed !== value.size
    || new Set(value.items.map(item => senseCardKey(item.sense, item.vocabulary.id))).size !== value.items.length) throw new Error('Invalid sense review session');
  if (value.pending !== null) {
    const pending = value.pending;
    const current = value.items[value.cursor];
    if (!record(pending) || !validItem(pending.item) || !statuses.has(String(pending.status))
      || !text(pending.requestId) || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(pending.requestId)
      || (pending.responseMs !== undefined && (!counter(pending.responseMs) || pending.responseMs < 500 || pending.responseMs > 600_000))
      || (pending.planMinutes !== undefined && (!counter(pending.planMinutes) || !allowedMinutes.has(pending.planMinutes)))
      || !current || senseCardKey(pending.item.sense, pending.item.vocabulary.id) !== senseCardKey(current.sense, current.vocabulary.id)
      || JSON.stringify(pending.item) !== JSON.stringify(current)) throw new Error('Invalid pending sense review');
  }
  return value as unknown as SenseReviewSession;
}

export function readSenseReviewSession(key: string, storage?: StorageLike): { session: SenseReviewSession | null; warning: string } {
  try {
    const raw = (storage ?? localStorage).getItem(key);
    if (!raw) return { session: null, warning: '' };
    try { return { session: parseSenseReviewSession(raw), warning: '' }; }
    catch { return { session: null, warning: '浏览器中的义项复习进度无法读取，将重新准备一组；已保存的学习记录仍在。' }; }
  } catch { return { session: null, warning: '浏览器存储不可用，可以查看义项，但提交复习前需要允许此站点保存数据。' }; }
}

export function writeSenseReviewSession(key: string, session: SenseReviewSession, storage?: StorageLike): void {
  try { (storage ?? localStorage).setItem(key, JSON.stringify(session)); }
  catch { throw new Error('无法保存义项复习进度。请允许此站点使用存储或释放空间后重试；尚未发送这次自评。'); }
}

export function newSenseReviewSession(items: SenseReviewItem[]): SenseReviewSession {
  return { version: 1, items, cursor: 0, size: items.length, assessed: 0, skipped: 0, removed: 0, pending: null };
}

export function reconcileSenseReviewSession(session: SenseReviewSession, due: SenseReviewItem[]): SenseReviewSession {
  const available = new Map(due.map(item => [senseCardKey(item.sense, item.vocabulary.id), item]));
  const remaining = session.items.slice(session.cursor).flatMap(item => {
    const key = senseCardKey(item.sense, item.vocabulary.id);
    if (session.pending && senseCardKey(session.pending.item.sense, session.pending.item.vocabulary.id) === key) return [session.pending.item];
    const fresh = available.get(key);
    return fresh ? [fresh] : [];
  });
  const removed = session.items.length - session.cursor - remaining.length;
  return { ...session, items: [...session.items.slice(0, session.cursor), ...remaining], removed: session.removed + removed };
}

export function finishSenseReviewCard(session: SenseReviewSession, result: 'assessed' | 'skipped' | 'removed'): SenseReviewSession {
  if (!session.items[session.cursor]) return session;
  if (result === 'removed') return { ...session, items: session.items.filter((_, index) => index !== session.cursor), removed: session.removed + 1, pending: null };
  return { ...session, cursor: session.cursor + 1, [result]: session[result] + 1, pending: null };
}
