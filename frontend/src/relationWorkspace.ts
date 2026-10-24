import type { StoredRelationType, Vocabulary, WordStatus } from './types';

export const relationWorkspaceLabels: Record<StoredRelationType, string> = {
  WORD_FAMILY: '同词族', SYNONYM: '近义词', ANTONYM: '反义词',
  CONCEPT: '概念关联', COLLOCATION: '常用搭配', DOMAIN_RELATED: '同领域',
};
export interface RelationWordState { vocabulary_id: number | null; status: WordStatus | null; meaning_summary: string | null }
export interface RelationContext { document_id: number; document_title: string; occurrence_id: number; sentence: string }
export interface WorkspaceRelation {
  id: number; source_word: string; target_word: string; relation_type: StoredRelationType;
  source: RelationWordState; target: RelationWordState; meaning: string | null; reason: string | null;
  provenance: 'llm' | 'offline' | 'unavailable'; batch_id: number | null; useful: boolean | null;
  context: RelationContext | null; updated_at: string;
}
export interface RelationWorkspace {
  summary: { total_relations: number; source_words: number; unfamiliar_targets: number; practice_pairs: number };
  sources: { word: string; meaning_summary: string | null; status: WordStatus | null; relation_count: number }[];
  items: WorkspaceRelation[];
  pagination: { page: number; page_size: number; total: number; total_pages: number };
  filters: { q: string; relation: StoredRelationType | 'all'; scope: 'all' | 'unfamiliar'; include_dismissed: boolean };
}
export interface RelationWorkspaceFilters {
  q: string; relation: StoredRelationType | 'all'; scope: 'all' | 'unfamiliar'; include_dismissed: boolean; page: number;
}

export function relationWorkspacePath(filters: RelationWorkspaceFilters): string {
  const params = new URLSearchParams({ q: filters.q.trim().slice(0, 100), relation: filters.relation,
    scope: filters.scope, include_dismissed: String(filters.include_dismissed), page: String(filters.page), page_size: '12' });
  return `/relation-workspace?${params}`;
}

/** Restrict exploration to an owned vocabulary entry, including a saved lemma spelling. */
export function findRelationSource(vocabulary: Vocabulary[], word: string): Vocabulary | null {
  const key = word.trim().toLowerCase();
  if (!key) return null;
  return vocabulary.find(item => item.lemma.toLowerCase() === key || item.word.toLowerCase() === key) ?? null;
}

/** Search a bounded menu, prioritising words still worth learning without claiming a score. */
export function relationSourceSuggestions(vocabulary: Vocabulary[], query: string, limit = 8): Vocabulary[] {
  const key = query.trim().toLowerCase();
  const rank = (item: Vocabulary) => item.status === 'UNKNOWN' ? 0 : item.status === 'UNCERTAIN' ? 1 : item.status === null ? 2 : 3;
  return vocabulary.filter(item => !key || item.word.toLowerCase().includes(key) || item.lemma.toLowerCase().includes(key))
    .sort((a, b) => Number(b.word.toLowerCase() === key) - Number(a.word.toLowerCase() === key) || rank(a) - rank(b) || b.id - a.id)
    .slice(0, limit);
}

/** A recall prompt must have an actual Chinese snapshot. It never uses a current dictionary guess. */
export function relationRecallMeaning(item: WorkspaceRelation): string | null {
  if (item.useful === false || !item.meaning?.trim() || !/\p{Script=Han}/u.test(item.meaning)
    || !item.source_word.trim() || !item.target_word.trim() || item.source_word.toLowerCase() === item.target_word.toLowerCase()) return null;
  const answer = item.target_word.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // A saved gloss can itself quote the English answer; mask it before rendering any prompt.
  return item.meaning.trim().replace(new RegExp(answer, 'gi'), '____');
}

export function relationPracticeItems(items: WorkspaceRelation[]): WorkspaceRelation[] {
  const seen = new Set<string>();
  return items.filter(item => {
    if (!relationRecallMeaning(item)) return false;
    const key = JSON.stringify([item.source_word.toLowerCase(), item.target_word.toLowerCase(), item.relation_type]);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
}

export function relationPageNumbers(page: number, total: number): (number | 'before' | 'after')[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const start = Math.min(Math.max(page - 1, 2), total - 4);
  const end = Math.max(Math.min(page + 1, total - 1), 5);
  return [1, ...(start > 2 ? ['before' as const] : []), ...Array.from({ length: end - start + 1 }, (_, index) => start + index),
    ...(end < total - 1 ? ['after' as const] : []), total];
}
