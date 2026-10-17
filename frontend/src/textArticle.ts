import type { Occurrence } from './types';

export const TEXT_IMPORT_LIMIT = 20_000;
export interface TextDraft { title: string; text: string }
export interface TextImportPayload extends TextDraft { request_id: string }
export type ArticlePart = { text: string; occurrence: Occurrence | null };

export function normalizeArticle(text: string): string { return text.replace(/\r\n?/g, '\n'); }
export function characterCount(text: string): number { return Array.from(text).length; }

export function textImportProblem(draft: TextDraft): string {
  const text = normalizeArticle(draft.text);
  if (!text.trim()) return '请先粘贴一篇英文文章。';
  if (characterCount(text) > TEXT_IMPORT_LIMIT) return '文章最多支持 20,000 字，请缩短后导入。';
  if (!/[A-Za-z]/.test(text)) return '文章中需要至少有一个英文单词。';
  if (characterCount(draft.title.trim()) > 250) return '文章标题最多支持 250 字。';
  if (/[\u0000-\u001f\u007f]/.test(draft.title)) return '标题不能包含换行或控制字符。';
  return '';
}

/** Python offsets count Unicode codepoints; never slice a JS UTF-16 string with them. */
export function articleParts(text: string, words: readonly Occurrence[]): ArticlePart[] {
  const characters = Array.from(text);
  const ordered = words.filter(word => Number.isSafeInteger(word.text_start) && Number.isSafeInteger(word.text_end))
    .slice().sort((a, b) => a.text_start! - b.text_start! || a.text_end! - b.text_end!);
  const parts: ArticlePart[] = [];
  let cursor = 0;
  const usedIds = new Set<number>();
  for (const word of ordered) {
    const start = word.text_start!;
    const end = word.text_end!;
    if (start < cursor || start < 0 || end <= start || end > characters.length || usedIds.has(word.id)) continue;
    const surface = characters.slice(start, end).join('');
    // An invalid offset must not change or hide the original article.
    if (surface !== word.word) continue;
    if (start > cursor) parts.push({ text: characters.slice(cursor, start).join(''), occurrence: null });
    parts.push({ text: surface, occurrence: word });
    usedIds.add(word.id);
    cursor = end;
  }
  if (cursor < characters.length) parts.push({ text: characters.slice(cursor).join(''), occurrence: null });
  return parts;
}
