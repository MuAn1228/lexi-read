import type { ReadingDocument } from './types';

export type ReadingSurface = 'text' | 'image';

export interface ReadingPosition {
  documentId: number;
  contentIdentity: string;
  selectedId: number | null;
  surface: ReadingSurface;
  pageNumber: number;
  zoom: number;
  anchorId: number | null;
  anchorOffset: number;
  anchorLeftOffset: number;
  fraction: number;
  horizontalFraction: number;
  updatedAt: number;
}

export interface ReadingResumePreferences {
  version: 1;
  lastDocumentId: number | null;
  positions: ReadingPosition[];
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;
const MAX_POSITIONS = 30;
const positiveId = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
const bounded = (value: unknown, min: number, max: number): value is number => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;

export const emptyReadingResume = (): ReadingResumePreferences => ({ version: 1, lastDocumentId: null, positions: [] });

function validPosition(value: unknown): value is ReadingPosition {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  return positiveId(item.documentId)
    && typeof item.contentIdentity === 'string' && item.contentIdentity.length > 0 && item.contentIdentity.length <= 200
    && !/[\p{Cc}\p{Cs}]/u.test(item.contentIdentity)
    && (item.selectedId === null || positiveId(item.selectedId))
    && (item.surface === 'text' || item.surface === 'image')
    && positiveId(item.pageNumber) && item.pageNumber <= 100
    && Number.isInteger(item.zoom) && Number(item.zoom) >= 75 && Number(item.zoom) <= 200 && Number(item.zoom) % 25 === 0
    && (item.anchorId === null || positiveId(item.anchorId))
    && bounded(item.anchorOffset, -10_000_000, 10_000_000)
    && bounded(item.anchorLeftOffset, -10_000_000, 10_000_000)
    && bounded(item.fraction, 0, 1)
    && bounded(item.horizontalFraction, 0, 1)
    && Number.isSafeInteger(item.updatedAt) && Number(item.updatedAt) >= 0 && Number(item.updatedAt) <= 8_640_000_000_000_000;
}

/** Browser state is optional and untrusted; a damaged entry never blocks reading. */
export function readReadingResume(key: string, storage?: StorageLike): { preferences: ReadingResumePreferences; available: boolean } {
  let raw: string | null;
  try { raw = (storage ?? localStorage).getItem(key); }
  catch { return { preferences: emptyReadingResume(), available: false }; }
  const fallback = { preferences: emptyReadingResume(), available: true };
  if (!raw || raw.length > 50_000) return fallback;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return fallback;
    const saved = value as Record<string, unknown>;
    if (saved.version !== 1 || !Array.isArray(saved.positions)) return fallback;
    const positions: ReadingPosition[] = [];
    const seen = new Set<number>();
    for (const item of saved.positions) {
      if (!validPosition(item) || seen.has(item.documentId)) continue;
      seen.add(item.documentId);
      positions.push({ ...item });
      if (positions.length === MAX_POSITIONS) break;
    }
    const lastDocumentId = saved.lastDocumentId === null || positiveId(saved.lastDocumentId)
      ? saved.lastDocumentId as number | null : null;
    return { preferences: { version: 1, lastDocumentId, positions }, available: true };
  } catch { return fallback; }
}

export function writeReadingResume(key: string, preferences: ReadingResumePreferences, storage?: StorageLike): boolean {
  try { (storage ?? localStorage).setItem(key, JSON.stringify(preferences)); return true; }
  catch { return false; }
}

export function rememberReadingDocument(preferences: ReadingResumePreferences, documentId: number): ReadingResumePreferences {
  if (!positiveId(documentId)) return preferences;
  return { ...preferences, lastDocumentId: documentId };
}

export function rememberReadingPosition(preferences: ReadingResumePreferences, position: ReadingPosition, makeLast = true): ReadingResumePreferences {
  if (!validPosition(position)) return preferences;
  return {
    version: 1,
    lastDocumentId: makeLast ? position.documentId : preferences.lastDocumentId,
    positions: [{ ...position }, ...preferences.positions.filter(item => item.documentId !== position.documentId)].slice(0, MAX_POSITIONS),
  };
}

export function forgetReadingDocument(preferences: ReadingResumePreferences, documentId: number): ReadingResumePreferences {
  return {
    ...preferences,
    lastDocumentId: preferences.lastDocumentId === documentId ? null : preferences.lastDocumentId,
    positions: preferences.positions.filter(item => item.documentId !== documentId),
  };
}

export function readingPositionForDocument(preferences: ReadingResumePreferences, documentId: number, contentIdentity: string): ReadingPosition | null {
  const position = preferences.positions.find(item => item.documentId === documentId);
  return position?.contentIdentity === contentIdentity ? { ...position } : null;
}

/** A compact content/occurrence identity invalidates browser anchors after proofreading rewrites them. */
export function readingDocumentIdentity(document: Pick<ReadingDocument, 'source' | 'ocr_text' | 'words' | 'pages' | 'width' | 'height'>): string {
  let hash = 2166136261;
  for (let index = 0; index < document.ocr_text.length; index++) hash = Math.imul(hash ^ document.ocr_text.charCodeAt(index), 16777619);
  const first = document.words[0]?.id ?? 0;
  const last = document.words.at(-1)?.id ?? 0;
  const pages = document.pages.map(page => `${page.number}:${page.text_start}:${page.text_end}`).join(',');
  for (let index = 0; index < pages.length; index++) hash = Math.imul(hash ^ pages.charCodeAt(index), 16777619);
  return `r1:${document.source}:${document.ocr_text.length}:${hash >>> 0}:${document.words.length}:${first}:${last}:${document.width}x${document.height}`;
}
