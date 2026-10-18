import type { DocumentPage, Occurrence, ReadingDocument } from './types';

export const PDF_IMPORT_LIMIT = 10 * 1024 * 1024;
export function pdfImportProblem(file: Pick<File, 'name' | 'type' | 'size'>): string {
  if (!/\.pdf$/i.test(file.name) || (file.type && !['application/pdf', 'application/x-pdf'].includes(file.type))) return '请选择 PDF 格式的英文文档。';
  if (file.size === 0) return '这个 PDF 是空文件，请重新选择。';
  if (file.size > PDF_IMPORT_LIMIT) return 'PDF 不能超过 10 MB，请缩小文件后重新上传。';
  return '';
}

export function pdfPageForOccurrence(document: Pick<ReadingDocument, 'pages' | 'words'>, id: number | null): number | null {
  const word = document.words.find(item => item.id === id);
  if (!word || !Number.isSafeInteger(word.text_start) || !Number.isSafeInteger(word.text_end)) return null;
  return document.pages.find(page => word.text_start! >= page.text_start && word.text_end! <= page.text_end)?.number ?? null;
}

/** Convert Python's global codepoint offsets to this page without changing occurrence IDs. */
export function pdfPageContent(text: string, words: readonly Occurrence[], page: DocumentPage): { text: string; words: Occurrence[] } {
  const characters = Array.from(text);
  const pageWords = words.filter(word => Number.isSafeInteger(word.text_start) && Number.isSafeInteger(word.text_end)
    && word.text_start! >= page.text_start && word.text_end! <= page.text_end && word.text_end! > word.text_start!);
  return {
    text: characters.slice(page.text_start, page.text_end).join(''),
    words: pageWords.map(word => ({ ...word, text_start: word.text_start! - page.text_start, text_end: word.text_end! - page.text_start })),
  };
}
