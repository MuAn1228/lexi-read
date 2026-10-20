export interface ProofreadPage { number: number; text: string }
export interface ProofreadDocument {
  document_id: number; revision: string; pages: ProofreadPage[]; max_characters: number;
}
export interface ProofreadPayload { request_id: string; base_revision: string; pages: ProofreadPage[] }

export function normalizeProofreadText(text: string): string { return text.replace(/\r\n?/g, '\n'); }

export function proofreadChangedPages(original: readonly ProofreadPage[], draft: readonly ProofreadPage[]): number[] {
  const originals = new Map(original.map(page => [page.number, page.text]));
  return draft.filter(page => originals.get(page.number) !== normalizeProofreadText(page.text)).map(page => page.number);
}

/** Include blank pages and the two separator codepoints between each original page. */
export function proofreadCharacterCount(pages: readonly ProofreadPage[]): number {
  return pages.reduce((count, page) => count + Array.from(normalizeProofreadText(page.text)).length, 0) + Math.max(0, pages.length - 1) * 2;
}

export function proofreadProblem(original: readonly ProofreadPage[], draft: readonly ProofreadPage[], maximum = 200_000): string {
  if (draft.length !== original.length || draft.some((page, index) => page.number !== original[index].number)) return '页码发生变化，请重新打开校对工作台。';
  if (proofreadCharacterCount(draft) > maximum) return `整篇正文最多支持 ${maximum.toLocaleString('en-US')} 字，请缩短后再保存。`;
  if (draft.some(page => /[\p{Cc}\p{Cs}]/u.test(normalizeProofreadText(page.text).replace(/[\n\t]/g, '')))) return '正文包含不可见控制字符，请删除后再保存。';
  if (!draft.some(page => /[A-Za-z]/.test(page.text))) return '请保留至少一个英文单词，空白页可以留空。';
  return '';
}

/** A complete immutable snapshot is retained for every retry, including empty original pages. */
export function proofreadPayload(document: ProofreadDocument, pages: readonly ProofreadPage[], requestId: string): ProofreadPayload {
  return { request_id: requestId, base_revision: document.revision, pages: pages.map(page => ({ number: page.number, text: normalizeProofreadText(page.text) })) };
}
