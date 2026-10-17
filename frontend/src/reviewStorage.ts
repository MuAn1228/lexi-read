import type { ReviewItem, WordStatus } from './types';

export interface PendingReview {
  item: ReviewItem;
  status: WordStatus;
  requestId: string;
}
export interface ReviewSession {
  version: 1;
  items: ReviewItem[];
  cursor: number;
  size: number;
  assessed: number;
  skipped: number;
  removed: number;
  pending: PendingReview | null;
}
type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;
const statuses = new Set(['KNOWN', 'UNKNOWN', 'UNCERTAIN']);
const positiveId = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
const counter = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const text = (value: unknown): value is string => typeof value === 'string';
const date = (value: unknown): value is string => text(value) && Number.isFinite(Date.parse(/[zZ]$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`));
const nullableDate = (value: unknown) => value === null || date(value);

function validItem(value: unknown): value is ReviewItem {
  if (!record(value) || !record(value.vocabulary) || !record(value.next_intervals_seconds)) return false;
  const word = value.vocabulary;
  const occurrence = value.occurrence;
  return positiveId(word.id) && positiveId(word.user_id) && text(word.word) && Boolean(word.word)
    && text(word.lemma) && Boolean(word.lemma) && (word.status === null || statuses.has(String(word.status)))
    && (word.mastery_score === null || (typeof word.mastery_score === 'number' && Number.isFinite(word.mastery_score) && word.mastery_score >= 0 && word.mastery_score <= 1))
    && counter(word.seen_count) && counter(word.lookup_count) && date(word.created_at) && date(word.updated_at)
    && nullableDate(word.first_seen_at) && nullableDate(word.last_seen_at)
    && nullableDate(value.last_reviewed_at) && nullableDate(value.due_at) && counter(value.known_streak)
    && [...statuses].every(status => typeof value.next_intervals_seconds === 'object'
      && positiveId((value.next_intervals_seconds as Record<string, unknown>)[status]))
    && (occurrence === null || (record(occurrence) && positiveId(occurrence.id) && positiveId(occurrence.document_id)
      && text(occurrence.document_title) && text(occurrence.word) && text(occurrence.sentence)));
}

/** Reject partial/corrupt state before any saved request can be replayed. */
export function parseReviewSession(raw: string): ReviewSession {
  const value: unknown = JSON.parse(raw);
  if (!record(value) || value.version !== 1 || !Array.isArray(value.items) || value.items.length > 10
    || !value.items.every(validItem) || !counter(value.cursor) || !counter(value.size) || value.size > 10
    || !counter(value.assessed) || !counter(value.skipped) || !counter(value.removed)
    || value.cursor > value.items.length || value.assessed + value.skipped !== value.cursor
    || value.items.length + value.removed !== value.size
    || new Set(value.items.map(item => item.vocabulary.id)).size !== value.items.length) throw new Error('Invalid review session');
  if (value.pending !== null) {
    const pending = value.pending;
    const current = value.items[value.cursor];
    if (!record(pending) || !validItem(pending.item) || !statuses.has(String(pending.status))
      || !text(pending.requestId) || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(pending.requestId)
      || !current || pending.item.vocabulary.id !== current.vocabulary.id
      || pending.item.vocabulary.word !== current.vocabulary.word || pending.item.vocabulary.lemma !== current.vocabulary.lemma
      || pending.item.occurrence?.document_id !== current.occurrence?.document_id) throw new Error('Invalid pending review');
  }
  return value as unknown as ReviewSession;
}

export function readReviewSession(key: string, storage?: StorageLike): { session: ReviewSession | null; warning: string } {
  try {
    const raw = (storage ?? localStorage).getItem(key);
    if (!raw) return { session: null, warning: '' };
    try { return { session: parseReviewSession(raw), warning: '' }; }
    catch { return { session: null, warning: '浏览器中的复习进度无法读取，将重新准备一组。已保存的学习记录仍在。' }; }
  } catch {
    return { session: null, warning: '浏览器存储不可用，可以查看待复习单词。请允许此站点保存数据后再提交自评。' };
  }
}

export function writeReviewSession(key: string, session: ReviewSession, storage?: StorageLike): void {
  try { (storage ?? localStorage).setItem(key, JSON.stringify(session)); }
  catch { throw new Error('无法保存浏览器复习进度。请允许此站点使用存储或释放空间后重试；尚未发送这次自评。'); }
}

export function newReviewSession(items: ReviewItem[]): ReviewSession {
  return { version: 1, items, cursor: 0, size: items.length, assessed: 0, skipped: 0, removed: 0, pending: null };
}

/** A pending request must survive even when the server already scheduled its word for the future. */
export function reconcileReviewSession(session: ReviewSession, due: ReviewItem[]): ReviewSession {
  const available = new Map(due.map(item => [item.vocabulary.id, item]));
  const remaining = session.items.slice(session.cursor).flatMap(item => {
    if (session.pending?.item.vocabulary.id === item.vocabulary.id) return [session.pending.item];
    const fresh = available.get(item.vocabulary.id);
    return fresh ? [fresh] : [];
  });
  const removed = session.items.length - session.cursor - remaining.length;
  return { ...session, items: [...session.items.slice(0, session.cursor), ...remaining], removed: session.removed + removed };
}

export function finishReviewCard(session: ReviewSession, result: 'assessed' | 'skipped' | 'removed'): ReviewSession {
  if (!session.items[session.cursor]) return session;
  if (result === 'removed') return {
    ...session, items: session.items.filter((_, index) => index !== session.cursor), removed: session.removed + 1, pending: null,
  };
  return { ...session, cursor: session.cursor + 1, [result]: session[result] + 1, pending: null };
}

export function reviewQuery(ids?: number[]): string {
  const query = new URLSearchParams({ limit: '10', tz_offset_minutes: String(-new Date().getTimezoneOffset()) });
  ids?.forEach(id => query.append('vocabulary_ids', String(id)));
  return `/review?${query}`;
}

export function intervalLabel(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时`;
  return `${Math.round(seconds / 86400)} 天`;
}
