import type { VocabularySense, VocabularySenses } from './types';

export function vocabularySensesPath(word: string, senseKey?: string): string {
  const base = `/vocabulary/${encodeURIComponent(word.trim())}/senses`;
  return senseKey === undefined ? base : `${base}/${encodeURIComponent(senseKey)}`;
}

export function senseReviewPath(ids: number[] | undefined, targetMinutes: 10 | 20 | 30): string {
  const query = new URLSearchParams({
    limit: '10',
    tz_offset_minutes: String(-new Date().getTimezoneOffset()),
    plan_minutes: String(targetMinutes),
  });
  ids?.forEach(id => query.append('sense_ids', String(id)));
  return `/sense-review?${query}`;
}

const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value));
const status = new Set(['KNOWN', 'UNKNOWN', 'UNCERTAIN']);

/** Keep optional sense reads degradable while rejecting executable or malformed payloads. */
export function usableVocabularySenses(value: unknown): VocabularySenses | null {
  if (!record(value) || typeof value.word !== 'string' || typeof value.lemma !== 'string' || !Array.isArray(value.senses)) return null;
  const senses = value.senses.filter((item): item is VocabularySense => record(item)
    && (item.id === null || (Number.isSafeInteger(item.id) && Number(item.id) > 0))
    && typeof item.sense_key === 'string' && /^[0-9a-f]{64}$/.test(item.sense_key)
    && (item.source === 'ecdict' || item.source === 'lexi_supplement')
    && typeof item.source_revision === 'string' && /^[0-9a-f]{64}$/.test(item.source_revision)
    && Number.isSafeInteger(item.source_ordinal) && Number(item.source_ordinal) >= 0 && Number(item.source_ordinal) <= 1000
    && (item.part_of_speech === null || (typeof item.part_of_speech === 'string' && item.part_of_speech.length > 0 && item.part_of_speech.length <= 80))
    && typeof item.meaning === 'string' && item.meaning.trim().length > 0 && item.meaning.length <= 10_000
    && (item.status === null || status.has(String(item.status)))
    && (item.mastery_score === null || (typeof item.mastery_score === 'number' && Number.isFinite(item.mastery_score) && item.mastery_score >= 0 && item.mastery_score <= 1))
    && (item.due_at === null || (typeof item.due_at === 'string' && Number.isFinite(Date.parse(item.due_at))))
    && Number.isSafeInteger(item.known_streak) && Number(item.known_streak) >= 0 && Number(item.known_streak) <= 5
    && typeof item.available === 'boolean');
  if (senses.length !== value.senses.length
      || new Set(senses.map(item => item.sense_key)).size !== senses.length) return null;
  return { word: value.word, lemma: value.lemma, senses };
}

export function senseCardKey(sense: Pick<VocabularySense, 'id' | 'sense_key'>, vocabularyId: number): string {
  return sense.id === null ? `word:${vocabularyId}:sense-key:${sense.sense_key}` : `sense:${sense.id}`;
}
