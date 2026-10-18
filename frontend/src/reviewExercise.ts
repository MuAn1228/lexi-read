import type { ReviewOccurrence } from './types';

export type ReviewMode = 'recall' | 'cloze';
export interface ReviewCloze { before: string; answer: string; after: string }

/** Server offsets identify this occurrence, rather than the first matching word. */
export function reviewCloze(occurrence: ReviewOccurrence | null): ReviewCloze | null {
  if (!occurrence?.word || !occurrence.sentence) return null;
  const { target_start: start, target_end: end } = occurrence;
  if (typeof start !== 'number' || typeof end !== 'number' || !Number.isSafeInteger(start) || !Number.isSafeInteger(end)) return null;
  const points = Array.from(occurrence.sentence);
  if (start < 0 || end <= start || end > points.length || points.slice(start, end).join('') !== occurrence.word) return null;
  return { before: points.slice(0, start).join(''), answer: occurrence.word, after: points.slice(end).join('') };
}

/** Match the original inflected form, ignoring case and typographic apostrophes. */
export function matchesReviewAnswer(input: string, answer: string): boolean {
  const normalize = (value: string) => value.trim().normalize('NFC').toLowerCase().replace(/[’‘]/g, "'");
  return Boolean(input.trim()) && normalize(input) === normalize(answer);
}

/** A changed occurrence or sentence must never inherit a prior card's revealed answer. */
export function reviewPracticeKey(vocabularyId: number, occurrence: ReviewOccurrence | null, mode: ReviewMode): string {
  return JSON.stringify([vocabularyId, occurrence?.id ?? null, occurrence?.sentence ?? '', occurrence?.word ?? '', occurrence?.target_start ?? null, occurrence?.target_end ?? null, mode]);
}
