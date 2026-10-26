const LOOKUP_MAX_LENGTH = 150;
const LOOKUP_HISTORY_LIMIT = 12;
const LOOKUP_HISTORY_STORAGE_MAX_LENGTH = 20_000;
const ENGLISH_LOOKUP = /^[A-Za-z]+(?:[' -][A-Za-z]+)*$/;

/** Preserve the reader's spelling while matching the dictionary's input rules. */
export function normalizeLookupQuery(text: string): string {
  if (typeof text !== 'string') return '';
  return text.trim().replace(/[‘’]/g, "'").replace(/–/g, '-').replace(/\s+/g, ' ');
}

/** An empty message means the normalized query is ready to submit. */
export function lookupQueryProblem(text: string): string {
  const query = normalizeLookupQuery(text);
  if (!query) return '请输入要查询的英文单词或短语。';
  if (query.length > LOOKUP_MAX_LENGTH) return '英文单词或短语最多 150 个字符，请缩短后再查询。';
  if (!ENGLISH_LOOKUP.test(query)) return '请输入有效英文单词或短语，仅支持英文字母及字母之间的空格、连字符和撇号。';
  return '';
}

function cleanLookupHistory(history: unknown, newest?: unknown): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  const append = (value: unknown) => {
    if (typeof value !== 'string') return;
    const query = normalizeLookupQuery(value);
    if (lookupQueryProblem(query)) return;
    const key = query.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    result.push(query);
  };

  append(newest);
  if (Array.isArray(history)) {
    for (const previous of history) {
      if (result.length >= LOOKUP_HISTORY_LIMIT) break;
      append(previous);
    }
  }
  return result;
}

/** Newest first; keep only valid, distinct queries without mutating the source. */
export function pushLookupHistory(history: readonly string[], word: string): string[] {
  return cleanLookupHistory(history, word);
}

/** Malformed data is empty history; unavailable storage is reported separately. */
export function readLookupHistory(
  key: string,
  storage?: Pick<Storage, 'getItem'>,
): { history: string[]; available: boolean } {
  let serialized: string | null;
  try {
    const historyStorage = storage ?? globalThis.localStorage;
    if (!historyStorage) return { history: [], available: false };
    serialized = historyStorage.getItem(key);
  } catch {
    return { history: [], available: false };
  }

  if (typeof serialized !== 'string' || serialized.length > LOOKUP_HISTORY_STORAGE_MAX_LENGTH) {
    return { history: [], available: true };
  }
  try {
    const parsed: unknown = JSON.parse(serialized);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { history: [], available: true };
    }
    const saved = parsed as { version?: unknown; words?: unknown };
    if (saved.version !== 1 || !Array.isArray(saved.words)) {
      return { history: [], available: true };
    }
    return { history: cleanLookupHistory(saved.words), available: true };
  } catch {
    return { history: [], available: true };
  }
}

/** Persist the same normalized, bounded history used in memory. */
export function writeLookupHistory(
  key: string,
  history: readonly string[],
  storage?: Pick<Storage, 'setItem'>,
): boolean {
  try {
    const historyStorage = storage ?? globalThis.localStorage;
    if (!historyStorage) return false;
    historyStorage.setItem(key, JSON.stringify({ version: 1, words: cleanLookupHistory(history) }));
    return true;
  } catch {
    return false;
  }
}
