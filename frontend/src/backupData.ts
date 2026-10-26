export interface BackupPreview {
  token: string; created_at: string; expires_at: string;
  document_count: number; vocabulary_count: number; event_count: number; file_count: number;
}
export interface BackupRestore {
  restored: boolean; safety_backup: string; safety_backup_url: string;
}
export interface RestoreOutcome extends BackupRestore { cleanup_warning?: string }
export interface AutomaticBackupStatus {
  database_ok: boolean; quick_check: string; foreign_key_check: string;
  last_success_at: string | null; last_size_bytes: number | null;
  backup_count: number; next_due_at: string | null; due: boolean;
  last_attempt_at: string | null; error: string | null; created: boolean; retention: number;
}

export function backupImportProblem(file: Pick<File, 'name' | 'size'>): string {
  if (!/\.zip$/i.test(file.name)) return '请选择由词间导出的 ZIP 备份文件。';
  if (file.size === 0) return '这个备份是空文件，请重新选择。';
  if (file.size > 250 * 1024 * 1024) return '备份文件不能超过 250 MB。';
  return '';
}

/** Attempt every key, so a failed reading preference never prevents clearing pending reviews. */
export function clearLearningPreferences(storage: Pick<Storage, 'removeItem' | 'getItem'>, reviewKey: string, documentKey: string, browsingKey?: string, lookupHistoryKey?: string, readingResumeKey?: string): string {
  const failed: string[] = [];
  const preferences = [[reviewKey, '未完成复习进度'], [documentKey, '上次阅读位置']];
  if (browsingKey) preferences.push([browsingKey, '词库浏览位置']);
  if (lookupHistoryKey) preferences.push([lookupHistoryKey, '查词历史']);
  if (readingResumeKey) preferences.push([readingResumeKey, '自动阅读续接']);
  for (const [key, label] of preferences) {
    try {
      storage.removeItem(key);
      if (storage.getItem(key) !== null) throw new Error('Browser retained the old value');
    } catch { failed.push(label); }
  }
  return failed.length ? `学习数据已恢复，但浏览器中的${failed.join('、')}未能清理。请允许浏览器存储后重新清理，再进入阅读空间。` : '';
}

export function backupDate(value: string): string {
  const normalized = /[zZ]$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? '时间不可用' : new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date);
}

export function backupSize(value: number | null): string {
  if (value === null) return '尚无备份';
  if (!Number.isFinite(value) || value < 0) return '大小不可用';
  if (value < 1024) return `${Math.round(value)} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: size < 10 ? 1 : 0 }).format(size)} ${units[unit]}`;
}

export function backupCheck(value: string): string {
  if (value === 'ok') return '正常';
  if (value === 'unavailable') return '暂无法核验';
  return '异常';
}

export function nextAutomaticBackup(status: AutomaticBackupStatus): string {
  if (!status.last_success_at) return '等待首次创建';
  if (status.due) return '已到期，将在本机启动时补建';
  return status.next_due_at ? backupDate(status.next_due_at) : '时间待确定';
}
