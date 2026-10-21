import type { Vocabulary } from './types';

export interface MaimemoRecord {
  voc_id: string; voc_spelling: string; study_count: number;
  first_study_date?: string | null; last_study_date?: string | null;
  next_study_date?: string | null; last_response?: string | null; tags: string[];
}
export interface MaimemoSource { records: MaimemoRecord[]; synced_at: string }
export type MaimemoSources = Record<string, MaimemoSource>;
export type MaimemoFilter = 'ALL' | 'MAIMEMO' | 'MAIMEMO_WEAK';
export function matchesMaimemoSource(source: MaimemoSource | undefined, filter: MaimemoFilter): boolean {
  if (filter === 'ALL') return true;
  if (!Array.isArray(source?.records) || !source.records.length) return false;
  return filter === 'MAIMEMO' || source.records.some(record => record && (record.tags?.includes('STICKING') || record.last_response === 'FORGET' || record.last_response === 'VAGUE'));
}
const responses: Record<string, string> = { FAMILIAR: '认识', VAGUE: '模糊', FORGET: '忘记', WELL_FAMILIAR: '熟知', CANCEL_WELL_FAMILIAR: '取消熟知' };
export function maimemoResponse(value: string | null | undefined): string {
  return value ? responses[value] ?? '暂无反馈' : '暂无反馈';
}
export function representativeRecord(source: MaimemoSource | undefined): MaimemoRecord | undefined {
  if (!Array.isArray(source?.records)) return undefined;
  return source.records.reduce<MaimemoRecord | undefined>((best, row) => row && (!best || row.study_count > best.study_count) ? row : best, undefined);
}
/** Plain word list for a Maimemo cloud notepad. This never writes to Maimemo. */
export function maimemoWordList(words: Vocabulary[]): string {
  return [...new Set(words.filter(word => word.status === 'UNKNOWN' || word.status === 'UNCERTAIN').map(word => word.lemma))].sort().join('\r\n');
}
export function downloadMaimemoWords(words: Vocabulary[]): void {
  const content = maimemoWordList(words);
  if (!content) throw new Error('当前筛选中没有需要巩固的词。');
  const url = URL.createObjectURL(new Blob(['\uFEFF' + content + '\r\n'], { type: 'text/plain;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = 'lexi-to-maimemo.txt';
  try { document.body.append(anchor); anchor.click(); }
  finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
}
