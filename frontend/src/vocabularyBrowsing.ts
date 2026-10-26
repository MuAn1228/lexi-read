import type { StatusFilter } from './WordList';
import type { MaimemoFilter } from './maimemoSources';

export const VOCABULARY_PAGE_SIZE = 100;

export interface VocabularyBrowsing {
  query: string;
  filter: StatusFilter;
  sourceFilter: MaimemoFilter;
  page: number;
  scrollY: number;
  expanded: number[];
  anchorId?: number;
  anchorOffset?: number;
}

export interface BrowsingPreferences {
  version: 1;
  resumeVocabulary: boolean;
  vocabulary: VocabularyBrowsing;
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;
export const emptyVocabularyBrowsing = (): VocabularyBrowsing => ({ query: '', filter: 'ALL', sourceFilter: 'ALL', page: 0, scrollY: 0, expanded: [] });
export const emptyBrowsingPreferences = (): BrowsingPreferences => ({ version: 1, resumeVocabulary: false, vocabulary: emptyVocabularyBrowsing() });
const counter = (value: unknown, max: number): value is number => Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= max;

/** Preferences are optional; damaged or older browser data must never block learning. */
export function readBrowsingPreferences(key: string, storage?: StorageLike): { preferences: BrowsingPreferences; available: boolean } {
  let raw: string | null;
  try { raw = (storage ?? localStorage).getItem(key); }
  catch { return { preferences: emptyBrowsingPreferences(), available: false }; }
  const fallback = { preferences: emptyBrowsingPreferences(), available: true };
  if (!raw || raw.length > 20_000) return fallback;
  try {
    const saved = JSON.parse(raw);
    if (saved?.version !== 1 || !saved.vocabulary || typeof saved.vocabulary !== 'object') return fallback;
    const value = saved.vocabulary;
    const anchor = counter(value.anchorId, Number.MAX_SAFE_INTEGER) && value.anchorId > 0
      && typeof value.anchorOffset === 'number' && Number.isFinite(value.anchorOffset) && Math.abs(value.anchorOffset) <= 10_000_000
      ? { anchorId: value.anchorId, anchorOffset: value.anchorOffset } : {};
    return { available: true, preferences: {
      version: 1, resumeVocabulary: saved.resumeVocabulary === true,
      vocabulary: {
        query: typeof value.query === 'string' ? value.query.slice(0, 1000) : '',
        filter: ['ALL', 'UNMARKED', 'KNOWN', 'UNKNOWN', 'UNCERTAIN'].includes(value.filter) ? value.filter : 'ALL',
        sourceFilter: ['ALL', 'MAIMEMO', 'MAIMEMO_WEAK'].includes(value.sourceFilter) ? value.sourceFilter : 'ALL',
        page: counter(value.page, 1_000_000) ? value.page : 0,
        scrollY: counter(value.scrollY, 10_000_000) ? value.scrollY : 0,
        expanded: Array.isArray(value.expanded) ? [...new Set<number>(value.expanded.filter((id: unknown) => counter(id, Number.MAX_SAFE_INTEGER) && id > 0))].slice(0, VOCABULARY_PAGE_SIZE) : [],
        ...anchor,
      },
    } };
  } catch { return fallback; }
}

export function writeBrowsingPreferences(key: string, preferences: BrowsingPreferences, storage?: StorageLike): boolean {
  try { (storage ?? localStorage).setItem(key, JSON.stringify(preferences)); return true; }
  catch { return false; }
}
