const LOOKUP_MAX_LENGTH = 150;
const LOOKUP_HISTORY_LIMIT = 12;
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

/** Newest first; keep only valid, distinct queries without mutating the source. */
export function pushLookupHistory(history: readonly string[], word: string): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  const append = (value: string) => {
    const query = normalizeLookupQuery(value);
    if (lookupQueryProblem(query)) return;
    const key = query.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    result.push(query);
  };

  append(word);
  if (Array.isArray(history)) {
    for (const previous of history) {
      if (result.length >= LOOKUP_HISTORY_LIMIT) break;
      append(previous);
    }
  }
  return result;
}
