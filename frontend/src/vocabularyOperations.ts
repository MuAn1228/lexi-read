import type { Vocabulary, WordStatus } from './types';

export const selectionLimit = 200;
export interface VocabularyGroup { id: number; name: string; vocabulary_ids: number[] }
export interface VocabularyBatch {
  request_id: string; status: WordStatus; items: { id: number; updated_at: string }[];
}
export interface VocabularyBatchResult {
  operation_id: string; items: Vocabulary[]; changed_count: number; undone: boolean;
}

/** Page additions preserve selections on other pages, and never silently exceed the limit. */
export function selectVocabularyPage(selected: Set<number>, ids: number[], checked: boolean): Set<number> {
  const next = new Set(selected);
  for (const id of ids) {
    if (!checked) next.delete(id);
    else if (next.size < selectionLimit) next.add(id);
  }
  return next;
}

/** Snapshot both versions and target status once; retries must reuse this exact payload. */
export function vocabularyBatch(words: Vocabulary[], status: WordStatus, requestId: string): VocabularyBatch {
  return { request_id: requestId, status, items: words.map(word => ({ id: word.id, updated_at: word.updated_at })) };
}

export function normalizeGroupName(name: string): string { return name.trim(); }
export function validGroupName(name: string): boolean {
  const count = [...normalizeGroupName(name)].length;
  return count >= 1 && count <= 30;
}
