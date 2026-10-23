import type { Recommendation } from './types';

export type ExpansionRelation = 'domain_related' | 'synonym' | 'word_family' | 'antonym' | 'collocation';
export type ExpansionRecommendation = Omit<Recommendation, 'warning'> & { warning?: string | null };
export type WordExpansionCache = Map<string, ExpansionRecommendation>;

export const expansionOptions: { relation: ExpansionRelation; label: string; description: string; primary: boolean }[] = [
  { relation: 'domain_related', label: '同类词', description: '从同一主题或类别联想，串起相关的词。', primary: true },
  { relation: 'synonym', label: '近义词', description: '比较意思相近的词，留意语气和使用场合。', primary: true },
  { relation: 'word_family', label: '派生词', description: '认识同词族里的构词关系；复数、过去式等词形变化不算派生词。', primary: true },
  { relation: 'antonym', label: '反义词', description: '用意义上的对照，帮助记住这个词。', primary: false },
  { relation: 'collocation', label: '常用搭配', description: '把词放进常见表达，记住它通常怎么用。', primary: false },
];

export function expansionLabel(relation: ExpansionRelation): string {
  return expansionOptions.find(option => option.relation === relation)!.label;
}

/** Keep the supplied word in one path segment, even if it contains punctuation. */
export function expansionPath(word: string): string {
  const value = word.trim();
  if (!value) throw new Error('请先选择一个单词。');
  return `/vocabulary/${encodeURIComponent(value)}/recommend`;
}

export function expansionCacheKey(word: string, relation: ExpansionRelation): string {
  return JSON.stringify([word.trim().toLowerCase(), relation]);
}

export function isExpansionRecommendation(value: unknown, relation: ExpansionRelation): value is ExpansionRecommendation {
  if (!value || typeof value !== 'object') return false;
  const result = value as Record<string, unknown>;
  if (!Number.isSafeInteger(result.batch_id) || (result.batch_id as number) < 1
    || !['llm', 'offline'].includes(result.source as string)
    || (result.warning != null && typeof result.warning !== 'string')
    || !Array.isArray(result.items) || result.items.length > 3) return false;
  const seen = new Set<string>();
  return result.items.every(value => {
    if (!value || typeof value !== 'object') return false;
    const item = value as Record<string, unknown>;
    if (typeof item.word !== 'string' || !item.word.trim() || typeof item.meaning !== 'string' || !item.meaning.trim()
      || typeof item.reason !== 'string' || !item.reason.trim() || item.relation !== relation
      || typeof item.score !== 'number' || !Number.isFinite(item.score) || item.score < 0 || item.score > 1
      || typeof item.priority !== 'number' || !Number.isFinite(item.priority) || item.priority < 0 || item.priority > 1) return false;
    const key = item.word.trim().toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** Unavailable/empty and offline fallbacks stay retryable rather than becoming AI cache hits. */
export function cacheExpansion(cache: WordExpansionCache, word: string, relation: ExpansionRelation, result: ExpansionRecommendation): boolean {
  if (result.source !== 'llm' || !result.items.length || !isExpansionRecommendation(result, relation)) return false;
  const key = expansionCacheKey(word, relation);
  cache.delete(key);
  cache.set(key, result);
  while (cache.size > 100) cache.delete(cache.keys().next().value!);
  return true;
}

export function expansionSourceLabel(result: ExpansionRecommendation, cached = false): string {
  if (result.source === 'offline') return '离线规则推荐';
  return cached ? 'AI 词汇拓展 · 本次已生成' : 'AI 词汇拓展';
}
