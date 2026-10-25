import type { DictionaryResult } from './dictionary';

/** A slower provider response must not erase a lesson or personal note already read. */
export function mergeReviewAnnotations(current: DictionaryResult | null, incoming: DictionaryResult): DictionaryResult {
  if (!current || current.word !== incoming.word || current.lemma !== incoming.lemma) return incoming;
  return {
    ...incoming,
    detail: incoming.detail ?? current.detail,
    cached: incoming.detail ? incoming.cached : current.detail ? current.cached : incoming.cached,
    maimemo: incoming.maimemo ?? current.maimemo,
  };
}

/** Fresh empty/error outcomes avoid repeated reads; expired content can refresh once. */
export function needsReviewAnnotations(result: DictionaryResult): boolean {
  return Boolean(result.can_fetch_annotations && (!result.maimemo || result.maimemo.stale));
}

/** Prefer a personal lesson with definitions and examples before asking Agnes. */
export function needsReviewLesson(result: DictionaryResult): boolean {
  const chinese = (value: string) => /[\u3400-\u9fff]/u.test(value);
  return Boolean(result.can_generate && !result.detail && !(
    result.maimemo?.interpretations.some(item => chinese(item.text)) &&
    result.maimemo.phrases.some(item => item.english.trim() && chinese(item.chinese))
  ));
}
