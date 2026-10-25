import type { Vocabulary } from './types';

export interface DictionaryExample { english: string; chinese: string }
export interface DictionaryDetail {
  word: string; lemma: string; summary: string;
  senses: { part_of_speech: string; meaning: string; explanation: string; examples: DictionaryExample[] }[];
  collocations: { phrase: string; meaning: string; example: string; translation: string }[];
  usage_notes: string[];
  confusables: { word: string; distinction: string }[];
}
export interface MaimemoAnnotations {
  source: 'maimemo_personal';
  status: 'available' | 'empty' | 'not_found' | 'unavailable';
  word: string;
  interpretations: { id: string; text: string; tags: string[] }[];
  phrases: { id: string; english: string; chinese: string; origin: string | null }[];
  notes: { id: string; type: string; text: string }[];
  cached: boolean; stale: boolean; fetched_at: string | null; warning: string | null;
}
export interface DictionaryResult {
  word: string; lemma: string;
  dictionary: {
    headword: string; phonetic: string; chinese: string[]; english: string[];
    forms: { label: string; word: string }[]; source: 'ecdict' | 'lexi_supplement'; source_url: string;
    examples?: DictionaryExample[]; usage_notes?: string[];
  } | null;
  detail: DictionaryDetail | null; cached: boolean; can_generate: boolean; warning: string | null;
  maimemo: MaimemoAnnotations | null; can_fetch_annotations: boolean;
}

/** Search cached Chinese meanings without interpreting a general definition as context. */
export function matchesVocabularyQuery(word: Vocabulary, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase();
  return !needle || [word.word, word.lemma, word.meaning_summary ?? '']
    .some(value => value.toLocaleLowerCase().includes(needle));
}

/** Each word is a single path segment, including words with punctuation. */
export function dictionaryPath(word: string, explain = false): string {
  return `/dictionary/${encodeURIComponent(word.trim())}${explain ? '/explain' : ''}`;
}

export function dictionaryAnnotationsPath(word: string): string {
  return `${dictionaryPath(word)}/annotations`;
}

export function hasMaimemoAnnotations(annotations: MaimemoAnnotations | null | undefined): boolean {
  return Boolean(annotations && (annotations.interpretations.length || annotations.phrases.length || annotations.notes.length));
}

export function dictionaryGenerationLabel(result: DictionaryResult): string {
  if (result.detail) return result.cached ? '已保存详细讲解' : '详细讲解已生成';
  return result.can_generate ? '生成详细讲解' : 'AI 详细讲解暂不可用';
}
