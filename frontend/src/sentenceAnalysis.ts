export interface SentenceClause { text: string; role: string; explanation: string }
export interface SentenceAnalysis {
  sentence: string; translation: string | null; structure: string | null;
  clauses: SentenceClause[]; key_points: string[];
  source: 'llm' | 'unavailable'; cached: boolean; warning: string | null;
}
export type SentenceAnalysisCache = Map<string, SentenceAnalysis>;
export const SENTENCE_CACHE_LIMIT = 32;
export function sentenceCacheKey(pageStart: number, sentence: string, position: number | string = 0): string {
  return JSON.stringify([pageStart, position, sentence]);
}
/** Offsets from Python count Unicode codepoints; ambiguous OCR spans stay occurrence-specific. */
export function sentenceOccurrenceKey(text: string, word: { id: number; word: string; sentence: string; text_start?: number | null; text_end?: number | null }, pageStart = 0, pageEnd?: number): string {
  const fallback = sentenceCacheKey(pageStart, word.sentence, `occurrence:${word.id}`);
  const characters = Array.from(text);
  const start = word.text_start, end = word.text_end;
  const limit = pageEnd ?? characters.length;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || !Number.isSafeInteger(pageStart) || !Number.isSafeInteger(limit)
    || pageStart < 0 || limit > characters.length || start! < pageStart || start! >= end! || end! > limit
    || characters.slice(start!, end!).join('') !== word.word) return fallback;
  const pageText = characters.slice(pageStart, limit).join('');
  let previousEnd = 0, previousPosition = pageStart;
  // Match the saved sentence segmentation, then verify its normalized text before sharing.
  for (const match of pageText.matchAll(/[^.!?]+(?:[.!?]+|$)/gu)) {
    const rawStart = previousPosition + Array.from(pageText.slice(previousEnd, match.index)).length;
    const rawEnd = rawStart + Array.from(match[0]).length;
    previousEnd = match.index + match[0].length; previousPosition = rawEnd;
    if (rawStart <= start! && end! <= rawEnd) {
      return match[0].replace(/\s+/gu, ' ').trim() === word.sentence
        ? sentenceCacheKey(pageStart, word.sentence, rawStart) : fallback;
    }
  }
  return fallback;
}
export function rememberSentenceAnalysis(cache: SentenceAnalysisCache | undefined, key: string, sentence: string, analysis: SentenceAnalysis): void {
  if (!cache || analysis.source !== 'llm' || analysis.sentence !== sentence) return;
  cache.delete(key);
  cache.set(key, analysis);
  while (cache.size > SENTENCE_CACHE_LIMIT) cache.delete(cache.keys().next().value!);
}
export const SENTENCE_CHARACTER_LIMIT = 4_000;
export function sentenceProblem(sentence: string): string {
  if (!sentence.trim()) return '这个单词暂时没有可解析的原句。';
  if (Array.from(sentence).length > SENTENCE_CHARACTER_LIMIT) return '本句超过 4,000 字，暂时无法解析。可以选择其他句子中的单词。';
  return '';
}
