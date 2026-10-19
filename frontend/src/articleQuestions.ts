import type { ReadingDocument } from './types';

export const ARTICLE_QUESTION_LIMIT = 500;
export const ARTICLE_TURN_LIMIT = 8;
export interface ArticleCitation {
  chunk_id: string; quote: string; text_start: number; text_end: number; page_number: number | null;
}
export interface ArticleAnswer {
  answer: string | null; citations: ArticleCitation[]; source: 'llm' | 'unavailable';
  answerable: boolean | null; cached: boolean; context_limited: boolean; warning: string | null;
}
export interface ArticleHistory { question: string; answer: string }
export interface ArticleTurn { id: number; question: string; response: ArticleAnswer }

export function articleQuestionProblem(question: string): string {
  const trimmed = question.trim();
  if (!trimmed) return '请先输入你想了解的问题。';
  if (Array.from(trimmed).length > ARTICLE_QUESTION_LIMIT) return '每个问题最多支持 500 字，请缩短后提问。';
  return '';
}

/** Failed and unsupported answers are never presented as facts in a follow-up. */
export function articleQuestionHistory(turns: readonly ArticleTurn[]): ArticleHistory[] {
  return turns.filter(turn => turn.response.source === 'llm' && turn.response.answerable === true && turn.response.answer?.trim())
    .slice(-3).map(turn => ({ question: turn.question, answer: turn.response.answer! }));
}

/** All article ranges use Python/Unicode codepoints, including OCR and PDF text. */
export interface ArticleFocus { text: string; start: number; end: number; token: number }
export interface HighlightPart { text: string; highlighted: boolean }

export function validArticleFocus(document: Pick<ReadingDocument, 'ocr_text' | 'source' | 'pages'>, focus: ArticleFocus | null): boolean {
  if (!focus || !Number.isSafeInteger(focus.start) || !Number.isSafeInteger(focus.end) || focus.start < 0 || focus.end <= focus.start) return false;
  const characters = Array.from(document.ocr_text);
  if (focus.end > characters.length || characters.slice(focus.start, focus.end).join('') !== focus.text) return false;
  return document.source !== 'pdf' || document.pages.some(page => focus.start >= page.text_start && focus.end <= page.text_end);
}

/** Split a rendered fragment without normalizing, dropping, or interpreting any source text. */
export function highlightParts(text: string, fragmentStart: number, focus: Pick<ArticleFocus, 'start' | 'end'> | null): HighlightPart[] {
  const characters = Array.from(text);
  const start = focus ? Math.max(0, focus.start - fragmentStart) : 0;
  const end = focus ? Math.min(characters.length, focus.end - fragmentStart) : 0;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || end <= start || start >= characters.length || end <= 0) return [{ text, highlighted: false }];
  const parts: HighlightPart[] = [];
  if (start > 0) parts.push({ text: characters.slice(0, start).join(''), highlighted: false });
  parts.push({ text: characters.slice(start, end).join(''), highlighted: true });
  if (end < characters.length) parts.push({ text: characters.slice(end).join(''), highlighted: false });
  return parts;
}
