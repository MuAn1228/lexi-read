import { AlertCircle, Archive, Check, CheckCircle2, Download, FileArchive, LoaderCircle, RefreshCw, ShieldCheck, Upload } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { errorMessage, request, requestFile } from './api';
import { backupDate, backupImportProblem } from './backupData';
import type { BackupPreview, RestoreOutcome } from './backupData';
import './dataManagement.css';

export default function DataManagementView({ disabled, onRestore, onClearBrowserState }: {
  disabled: boolean; onRestore: (token: string) => Promise<RestoreOutcome>; onClearBrowserState: () => Promise<string>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<BackupPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [operation, setOperation] = useState('');
  const [error, setError] = useState('');
  const [outcome, setOutcome] = useState<RestoreOutcome | null>(null);
  const [expired, setExpired] = useState(false);
  const [restoreAttempted, setRestoreAttempted] = useState(false);
  const pending = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const objectUrl = useRef<string | null>(null);
  const busy = disabled || Boolean(operation);
  useEffect(() => () => {
    controller.current?.abort();
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
  }, []);
  useEffect(() => {
    if (!preview) { setExpired(false); return; }
    const delay = Date.parse(preview.expires_at) - Date.now();
    setExpired(delay <= 0);
    if (delay > 0) { const timer = setTimeout(() => setExpired(true), Math.min(delay, 2_147_483_647)); return () => clearTimeout(timer); }
  }, [preview]);
  const selectFile = (value?: File) => {
    if (!value || pending.current || disabled) return;
    const problem = backupImportProblem(value);
    if (problem) { setError(problem); return; }
    setFile(value); setPreview(null); setConfirmed(false); setError('');
  };
  const run = async (name: string, action: (signal: AbortSignal) => Promise<void>) => {
    if (pending.current || disabled) return;
    pending.current = true; setOperation(name); setError('');
    const current = new AbortController(); controller.current = current;
    try { await action(current.signal); }
    catch (e) { if (!current.signal.aborted) setError(errorMessage(e)); }
    finally { if (!current.signal.aborted) { pending.current = false; setOperation(''); controller.current = null; } }
  };
  const download = async (path: string, name: string, signal: AbortSignal) => {
    const blob = await requestFile(path, { signal });
    if (signal.aborted) return;
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    const url = URL.createObjectURL(blob); objectUrl.current = url;
    const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  };
  const previewBackup = () => {
    if (!file) return;
    void run('preview', async signal => {
      const form = new FormData(); form.append('file', file);
      const value = await request<BackupPreview>('/backups/preview', { method: 'POST', body: form, signal });
      if (!signal.aborted) { setPreview(value); setConfirmed(false); }
    });
  };
  const restoreBackup = () => {
    if (!preview || !confirmed || expired) return;
    void run('restore', async () => {
      setRestoreAttempted(true);
      const value = await onRestore(preview.token);
      setOutcome(value); setPreview(null); setFile(null); setConfirmed(false);
    });
  };
  return <section className="data-management" aria-label="数据备份与恢复">
    {outcome ? <div className="panel restore-complete"><span className="restore-success-icon"><CheckCircle2 size={33} /></span><h2>学习数据已恢复</h2><p>文章、附件、词库和学习记录已切换到备份中的数据。Agnes 等模型配置保留，{outcome.cleanup_warning ? '当前浏览器的旧复习进度还需要清理。' : '当前浏览器的未完成复习进度已重置。'}</p><div className="backup-safety-note"><ShieldCheck size={20} /><p>恢复前的数据已自动保存。建议下载这份备份，留一份回退记录。</p></div>{outcome.cleanup_warning && <div className="error-banner" role="alert"><AlertCircle size={18} /><span>{outcome.cleanup_warning}</span><button className="text-button" disabled={Boolean(operation)} onClick={() => void run('clear', async () => { const warning = await onClearBrowserState(); setOutcome(value => value ? { ...value, cleanup_warning: warning || undefined } : value); })}><RefreshCw size={14} />重新清理浏览器进度</button></div>}{error && <p className="modal-error" role="alert">{error}</p>}<div className="backup-complete-actions"><button className="button secondary" disabled={Boolean(operation)} onClick={() => void run('safety', signal => download(outcome.safety_backup_url, 'lexi-before-restore.zip', signal))}>{operation === 'safety' ? <LoaderCircle size={16} className="spin" /> : <Download size={16} />}下载恢复前备份</button><button className="button primary" disabled={Boolean(operation) || Boolean(outcome.cleanup_warning)} onClick={() => window.location.reload()}>重新进入阅读空间</button></div></div>
      : <><div className="backup-intro"><Archive size={21} /><div><h2>把学习积累，妥善保存</h2><p>完整备份文章、原始附件、词库、标签、书签、学习记录和复习排期。备份不包含 API Key 等模型配置。</p></div></div><div className="backup-grid"><section className="panel backup-export"><div className="panel-heading"><div><Download size={17} /><h3>导出完整备份</h3></div></div><div className="backup-card-content"><p>下载一份 ZIP 文件，保存在自己的电脑或移动硬盘中。之后可以在词间本地版恢复。</p><button className="button primary" disabled={busy} onClick={() => void run('export', signal => download('/api/backups/export', `lexi-backup-${new Date().toISOString().slice(0, 10)}.zip`, signal))}>{operation === 'export' ? <LoaderCircle size={16} className="spin" /> : <Download size={16} />}下载完整备份</button></div></section><section className="panel backup-import"><div className="panel-heading"><div><Upload size={17} /><h3>从备份恢复</h3></div></div><div className="backup-card-content"><p>选择词间导出的 ZIP，先核对内容，再决定是否覆盖。选择文件和预览不会改变当前学习数据。</p><input ref={input} type="file" accept=".zip,application/zip,application/x-zip-compressed" className="visually-hidden" aria-label="选择词间备份文件" disabled={busy} onChange={event => { selectFile(event.target.files?.[0]); event.target.value = ''; }} /><div className="backup-file-picker"><FileArchive size={23} /><div><strong>{file?.name ?? '尚未选择备份文件'}</strong><span>{file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : 'ZIP · 最大 250 MB'}</span></div><button className="button secondary" disabled={busy} onClick={() => input.current?.click()}>{file ? '重新选择备份' : '选择备份文件'}</button></div><button className="button secondary" disabled={busy || !file} onClick={previewBackup}>{operation === 'preview' ? <LoaderCircle size={16} className="spin" /> : <FileArchive size={16} />}预览备份内容</button></div></section></div>
        {error && <div className="error-banner" role="alert"><AlertCircle size={18} /><span>{error}</span></div>}
        {restoreAttempted && <p className="backup-expired" role="status">恢复请求已发出。为避免沿用旧学习进度，完成前会停留在此页；如响应中断，可使用当前预览重试确认。</p>}
        {preview && <section className="panel backup-preview" aria-label="备份内容预览"><div className="panel-heading"><div><Check size={17} /><h3>核对这份备份</h3></div></div><div className="backup-preview-content"><p className="backup-created">备份时间：{backupDate(preview.created_at)}</p><dl className="backup-counts"><div><dt>文章</dt><dd>{preview.document_count}</dd></div><div><dt>词汇</dt><dd>{preview.vocabulary_count}</dd></div><div><dt>学习记录</dt><dd>{preview.event_count}</dd></div><div><dt>附件文件</dt><dd>{preview.file_count}</dd></div></dl><div className="backup-safety-note"><ShieldCheck size={20} /><p>恢复会覆盖当前本地学习数据，并先自动保存恢复前的完整备份。模型配置保留；当前浏览器的未完成复习进度会重置。</p></div>{expired && <p className="backup-expired" role="status">预览已过期，请点击“预览备份内容”重新检查；所选文件已保留。</p>}<label className="backup-confirm"><input type="checkbox" aria-label="用备份覆盖当前学习数据" checked={confirmed} disabled={busy || expired} onChange={event => setConfirmed(event.target.checked)} /><span>用备份覆盖当前学习数据</span></label><button className="button primary" disabled={busy || !confirmed || expired} onClick={restoreBackup}>{operation === 'restore' ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}确认恢复备份</button><p className="backup-expiry">此预览有效至 {backupDate(preview.expires_at)}；恢复失败可保留当前预览重试。</p></div></section>}
      </>}
  </section>;
}
