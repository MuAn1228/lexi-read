import type { ReadingDocument } from './types';

export function isImageArticle(source: string): boolean { return source === 'upload' || source === 'demo'; }

/** A manual correction has exact text offsets but no physical positions in the original image. */
export function hasCorrectedImageText(document: Pick<ReadingDocument, 'source' | 'words'>): boolean {
  return isImageArticle(document.source) && document.words.some(word => word.bbox_source === 'corrected_text');
}
