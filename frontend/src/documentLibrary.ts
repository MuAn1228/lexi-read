import type { DocumentSummary, ReadingDocument } from './types';

export interface DocumentPatch { title?: string; tags?: string[]; bookmark_occurrence_id?: number | null }
export interface LibraryFilters { q: string; tag: string; source: string; sort: string; deleted: boolean }
export function libraryPath(filters: LibraryFilters): string {
  const query = new URLSearchParams({ sort: filters.sort });
  if (filters.q.trim()) query.set('q', filters.q.trim());
  if (filters.tag) query.set('tag', filters.tag);
  if (filters.source) query.set('source', filters.source);
  if (filters.deleted) query.set('deleted', 'true');
  return `/documents?${query}`;
}
export function articleSource(source: string): string {
  return ({ text: '文字文章', upload: '图片文章', demo: '示例文章', pdf: 'PDF 文档', web: '网页文章' } as Record<string, string>)[source] ?? '阅读材料';
}
export function articleEdit(title: string, tags: readonly string[], added: string): { patch?: DocumentPatch; error?: string } {
  const normalizedTitle = title.trim();
  if (!normalizedTitle) return { error: '请填写文章标题。' };
  if (Array.from(normalizedTitle).length > 250) return { error: '文章标题最多支持 250 字。' };
  if (/[\u0000-\u001f\u007f]/.test(title)) return { error: '标题不能包含换行或控制字符。' };
  const normalizedTags: string[] = [];
  const seen = new Set<string>();
  for (const value of [...tags, ...added.split(/[,，]/)]) {
    const tag = value.trim();
    if (!tag) continue;
    if (Array.from(tag).length > 20) return { error: '每个标签最多支持 20 字。' };
    if (/[\u0000-\u001f\u007f]/.test(tag)) return { error: '标签不能包含换行或控制字符。' };
    const key = tag.toLowerCase();
    if (seen.has(key)) continue;
    normalizedTags.push(tag); seen.add(key);
  }
  if (normalizedTags.length > 10) return { error: '每篇文章最多添加 10 个标签。' };
  return { patch: { title: normalizedTitle, tags: normalizedTags } };
}
export function activeArticleList(items: readonly DocumentSummary[], updated: DocumentSummary): DocumentSummary[] {
  if (updated.deleted_at) return items.filter(item => item.id !== updated.id);
  const exists = items.some(item => item.id === updated.id);
  return exists ? items.map(item => item.id === updated.id ? updated : item)
    : [updated, ...items].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id);
}
/** Review links explicitly select their occurrence; ordinary opens resume a saved bookmark. */
export function initialArticleSelection(document: ReadingDocument, occurrenceId?: number): number | null {
  const exists = (id: number | null | undefined) => document.words.find(word => word.id === id)?.id;
  return exists(occurrenceId) ?? exists(document.bookmark_occurrence_id) ?? document.words[0]?.id ?? null;
}
