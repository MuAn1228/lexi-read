export interface SentenceClause { text: string; role: string; explanation: string }
export interface SentenceAnalysis {
  sentence: string; translation: string | null; structure: string | null;
  clauses: SentenceClause[]; key_points: string[];
  source: 'llm' | 'unavailable'; cached: boolean; warning: string | null;
}
export const SENTENCE_CHARACTER_LIMIT = 4_000;
export function sentenceProblem(sentence: string): string {
  if (!sentence.trim()) return '这个单词暂时没有可解析的原句。';
  if (Array.from(sentence).length > SENTENCE_CHARACTER_LIMIT) return '本句超过 4,000 字，暂时无法解析。可以选择其他句子中的单词。';
  return '';
}
