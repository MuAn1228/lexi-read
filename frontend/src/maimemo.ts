export interface MaimemoStatus {
  configured: boolean;
  imported_words: number;
  last_synced_at: string | null;
}

export interface MaimemoPreview {
  preview_id: string;
  expires_at: string;
  remote_total: number | null;
  fetched_count: number;
  studied_count: number;
  planned_count: number;
  valid_words: number;
  invalid_words: number;
  complete: boolean;
  warning: string | null;
  sample_words: string[];
}

export interface MaimemoImportResult {
  added: number;
  existing: number;
  source_updated: number;
  duplicate_records: number;
  invalid_words: number;
  imported_records: number;
  complete: boolean;
  warning: string | null;
}

/** Offset-free backend timestamps are UTC, matching the rest of the app. */
export function maimemoPreviewRemaining(preview: Pick<MaimemoPreview, 'expires_at'> | null, now = Date.now()): number {
  if (!preview) return 0;
  const value = preview.expires_at;
  const normalized = /[zZ]$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
  const expiry = Date.parse(normalized);
  return Number.isFinite(expiry) ? Math.max(0, expiry - now) : 0;
}

/** Record counts describe scope; only valid_words is a count of unique lemmas. */
export function maimemoImportProblem(preview: MaimemoPreview | null, includePlanned: boolean, now = Date.now()): string {
  if (!preview) return '请先读取墨墨词库并核对预览。';
  if (maimemoPreviewRemaining(preview, now) <= 0) return '预览已过期，请重新读取墨墨词库后再导入。';
  if (preview.valid_words <= 0) return '本次没有可导入的有效英文词汇，请重新读取或检查墨墨词库。';
  if (!includePlanned && preview.studied_count <= 0) return '当前范围没有有学习经历的记录；可勾选未学习的规划词，或重新读取。';
  return '';
}

/** A missing remote warning must never conceal an incomplete fetch. */
export function maimemoWarning(value: Pick<MaimemoPreview, 'complete' | 'warning'>): string {
  const warning = value.warning?.trim() ?? '';
  if (!value.complete) {
    const partial = '墨墨词库尚未完整读取，本次仅包含已读取的部分。可重新读取后补充其余记录。';
    return warning ? `${partial} ${warning}` : partial;
  }
  return warning;
}
