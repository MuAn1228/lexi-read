export interface ArticleVocabularySheetItem {
  word: string;
  lemma: string;
  article_word: string;
  phonetic: string | null;
  meanings: string[];
  count: number;
  first_occurrence_id: number;
}

export interface ArticleVocabularySheetData {
  document_id: number;
  title: string;
  item_count: number;
  occurrence_count: number;
  items: ArticleVocabularySheetItem[];
  dictionary_warning?: string | null;
}

export function articleVocabularySheetPath(documentId: number): string {
  return `/documents/${documentId}/vocabulary-sheet`;
}

export function vocabularySheetItemKey(item: ArticleVocabularySheetItem): number {
  return item.first_occurrence_id;
}

export function selectedVocabularySheetItems(
  items: readonly ArticleVocabularySheetItem[],
  selected: ReadonlySet<number>,
): ArticleVocabularySheetItem[] {
  return items.filter(item => selected.has(vocabularySheetItemKey(item)));
}

function oneLine(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

export function vocabularySheetMeaning(item: ArticleVocabularySheetItem): string {
  const meanings = item.meanings.map(oneLine).filter(Boolean);
  return meanings.length ? meanings.join('；') : '暂无本地释义';
}

export function vocabularySheetHeadword(item: ArticleVocabularySheetItem): string {
  return oneLine(item.word || item.lemma || item.article_word);
}

export function articleVocabularySheetText(title: string, items: readonly ArticleVocabularySheetItem[]): string {
  const heading = oneLine(title) || '未命名文章';
  const rows = items.map((item, index) => {
    const phonetic = item.phonetic?.replace(/^\/+|\/+$/g, '').trim();
    const pronunciation = phonetic ? ` /${phonetic}/` : '';
    const occurrences = item.count > 1 ? ` 〔出现 ${item.count} 次〕` : '';
    return `${String(index + 1).padStart(3, '0')}. ${vocabularySheetHeadword(item)}${pronunciation}  ${vocabularySheetMeaning(item)}${occurrences}`;
  });
  return [heading, `待收录词汇表 · ${items.length} 个词 · 按文章首次出现排序`, '', ...rows, ''].join('\r\n');
}

export function articleVocabularySheetFilename(title: string): string {
  const safe = oneLine(title)
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-')
    .replace(/-+/g, '-')
    .replace(/^[.\s-]+|[.\s-]+$/g, '')
    .slice(0, 60) || '当前文章';
  return `${safe}-待收录词汇表.txt`;
}

export function downloadArticleVocabularySheet(title: string, items: readonly ArticleVocabularySheetItem[]): void {
  const url = URL.createObjectURL(new Blob(['\uFEFF' + articleVocabularySheetText(title, items)], { type: 'text/plain;charset=utf-8' }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = articleVocabularySheetFilename(title);
  try {
    document.body.append(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
