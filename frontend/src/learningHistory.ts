import type { WordStatus } from './types';

export type HistoryRange = 7 | 30 | 90;
export type HistoryKind = 'all' | 'review' | 'lookup' | 'mark' | 'related';
export interface HistoryFilters { day: string | null; kind: HistoryKind; q: string; page: number }
export interface HistoryContext { document_id: number; document_title: string; occurrence_id: number; sentence: string }
export interface HistoryDay { date: string; words: number; reviews: number; lookups: number; marks: number; actions: number }
export interface HistoryFocusWord {
  vocabulary_id: number; word: string; lemma: string; status: WordStatus | null; meaning_summary: string | null;
  unfamiliar_count: number; lookup_count: number; review_unfamiliar_count: number; last_at: string; context: HistoryContext | null;
}
export interface HistoryEntry {
  key: string; date: string; word: string; lemma: string; vocabulary_id: number | null;
  status: WordStatus | null; meaning_summary: string | null; last_at: string;
  actions: number; review_count: number; lookup_count: number; mark_count: number; related_count: number;
  last_event_type: string; last_was_review: boolean; context: HistoryContext | null;
}
export interface LearningHistory {
  as_of: string; range: { days: HistoryRange; start_date: string; end_date: string };
  summary: { active_days: number; studied_words: number; reviewed_words: number; lookup_count: number; mark_count: number; total_actions: number; due_total: number };
  days: HistoryDay[]; focus_words: HistoryFocusWord[]; entries: HistoryEntry[];
  pagination: { page: number; page_size: number; total: number; total_pages: number };
  filters: { day: string | null; kind: HistoryKind; q: string };
}

/** All requests share the device's local calendar, including the near-midnight boundary. */
export function learningHistoryPath(days: HistoryRange, filters: HistoryFilters, offset = -new Date().getTimezoneOffset()): string {
  const params = new URLSearchParams({ days: String(days), tz_offset_minutes: String(offset), kind: filters.kind,
    page: String(filters.page), page_size: '20' });
  if (filters.day) params.set('day', filters.day);
  if (filters.q.trim()) params.set('q', filters.q.trim().slice(0, 100));
  return `/learning-history?${params}`;
}

/** A date-only value is not a UTC instant; format it without shifting its calendar date. */
export function historyDateLabel(date: string, long = false): string {
  const value = new Date(`${date}T12:00:00Z`);
  if (Number.isNaN(value.getTime())) return date;
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'UTC', month: 'long', day: 'numeric', ...(long ? { weekday: 'long' } : {}) }).format(value);
}

/** Monday-first columns keep partial weeks and year/month boundaries in place. */
export function historyCalendarWeeks(days: readonly HistoryDay[]): (HistoryDay | null)[][] {
  if (!days.length) return [];
  const first = new Date(`${days[0].date}T12:00:00Z`);
  if (Number.isNaN(first.getTime())) return [];
  const slots: (HistoryDay | null)[] = Array.from({ length: (first.getUTCDay() + 6) % 7 }, () => null);
  slots.push(...days);
  while (slots.length % 7) slots.push(null);
  return Array.from({ length: slots.length / 7 }, (_, index) => slots.slice(index * 7, index * 7 + 7));
}

export function historyActivityLevel(actions: number): number {
  return actions <= 0 ? 0 : actions <= 2 ? 1 : actions <= 5 ? 2 : actions <= 10 ? 3 : 4;
}

export function groupHistoryEntries(entries: readonly HistoryEntry[]): { date: string; entries: HistoryEntry[] }[] {
  const groups = new Map<string, HistoryEntry[]>();
  for (const entry of entries) {
    const group = groups.get(entry.date);
    if (group) group.push(entry); else groups.set(entry.date, [entry]);
  }
  return Array.from(groups, ([date, items]) => ({ date, entries: items }));
}

export function historyLatestLabel(type: string, review: boolean): string {
  const marks: Record<string, string> = { MARK_KNOWN: '已掌握', MARK_UNKNOWN: '不认识', MARK_UNCERTAIN: '不确定' };
  if (marks[type]) return `${review ? '复习自评' : '标记'}：${marks[type]}`;
  return ({ LOOKUP: '主动查看语境释义', VIEW_RELATED: '查看关联词', RECOMMENDATION_GENERATED: '生成关联词',
    RECOMMENDATION_USEFUL: '认为关联有用', RECOMMENDATION_NOT_USEFUL: '认为关联没用' } as Record<string, string>)[type] ?? '学习操作';
}

export function historyPageNumbers(current: number, total: number): (number | 'before' | 'after')[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  if (current <= 4) return [1, 2, 3, 4, 5, 'after', total];
  if (current >= total - 3) return [1, 'before', total - 4, total - 3, total - 2, total - 1, total];
  return [1, 'before', current - 1, current, current + 1, 'after', total];
}
