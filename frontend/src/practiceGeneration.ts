export type PracticeDifficulty = 'A2' | 'B1' | 'B2';
export type PracticeLength = 'short' | 'medium' | 'long';
export interface PracticeTarget {
  id: number; word: string; lemma: string; status: 'KNOWN' | 'UNKNOWN' | 'UNCERTAIN';
  reason: 'due' | 'unknown' | 'uncertain'; mastery_score: number | null; due_at: string | null;
}
export interface PracticeTargets { targets: PracticeTarget[]; limit: number }
export interface PracticeOptions { topic: string; difficulty: PracticeDifficulty; length: PracticeLength }
export interface PracticePayload extends PracticeOptions { request_id: string; vocabulary_ids: number[] }
export interface PracticeMatch { vocabulary_id: number; word: string; text_start: number; text_end: number }
export interface PracticeResult extends PracticeOptions {
  request_id: string; source: 'llm' | 'unavailable'; title: string | null; text: string | null;
  targets: PracticeTarget[]; matches: PracticeMatch[]; word_count: number | null; cached: boolean; warning: string | null;
}
export interface PracticePart { text: string; vocabularyId: number | null }
export const PRACTICE_LENGTH_LABELS: Record<PracticeLength, string> = { short: '80–120 词', medium: '150–200 词', long: '250–300 词' };

export function practiceProblem(options: PracticeOptions, targets: readonly PracticeTarget[]): string {
  const topic = options.topic.trim();
  if (!topic) return '请写下你想读的主题。';
  if (Array.from(topic).length > 100) return '主题最多支持 100 字，请缩短后生成。';
  if (/[\u0000-\u001f\u007f]/.test(topic)) return '主题不能包含换行或控制字符。';
  if (!targets.length) return '还没有可用于练习的目标词，请先在阅读中标记不熟悉的词。';
  if (targets.length > 5 || new Set(targets.map(target => target.id)).size !== targets.length) return '目标词暂时无法使用，请刷新目标词后重试。';
  return '';
}

export function practiceTargetReason(target: PracticeTarget): string {
  if (target.reason === 'due') return '已到复习时间';
  return target.reason === 'unknown' ? '目前不认识' : '目前不确定';
}

function validPracticeMatches(text: string, matches: readonly PracticeMatch[], targets: readonly PracticeTarget[]): PracticeMatch[] {
  const characters = Array.from(text);
  const allowed = new Set(targets.map(target => target.id));
  return matches.filter(match => allowed.has(match.vocabulary_id)
    && Number.isSafeInteger(match.text_start) && Number.isSafeInteger(match.text_end)
    && match.text_start >= 0 && match.text_end > match.text_start && match.text_end <= characters.length
    && characters.slice(match.text_start, match.text_end).join('') === match.word);
}

/** Render literal source text with verified server Unicode ranges; never interpolate HTML. */
export function practiceParts(text: string, matches: readonly PracticeMatch[], targets: readonly PracticeTarget[]): PracticePart[] {
  const characters = Array.from(text);
  const ordered = validPracticeMatches(text, matches, targets).sort((a, b) => a.text_start - b.text_start || a.text_end - b.text_end);
  const merged: PracticeMatch[] = [];
  for (const match of ordered) {
    const previous = merged[merged.length - 1];
    if (previous && match.text_start <= previous.text_end) previous.text_end = Math.max(previous.text_end, match.text_end);
    else merged.push({ ...match });
  }
  const parts: PracticePart[] = [];
  let cursor = 0;
  for (const match of merged) {
    const start = match.text_start; const end = match.text_end;
    const surface = characters.slice(start, end).join('');
    if (start > cursor) parts.push({ text: characters.slice(cursor, start).join(''), vocabularyId: null });
    parts.push({ text: surface, vocabularyId: match.vocabulary_id }); cursor = end;
  }
  if (cursor < characters.length) parts.push({ text: characters.slice(cursor).join(''), vocabularyId: null });
  return parts;
}

/** Only genuine, fully covered AI previews may become reading articles. */
export function usablePractice(result: PracticeResult | null): result is PracticeResult & { title: string; text: string } {
  if (!result || result.source !== 'llm' || !result.title?.trim() || !result.text?.trim() || !result.targets.length) return false;
  const covered = new Set(validPracticeMatches(result.text, result.matches, result.targets).map(match => match.vocabulary_id));
  return result.targets.every(target => covered.has(target.id));
}
