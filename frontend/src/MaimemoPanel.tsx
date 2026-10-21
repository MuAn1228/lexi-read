import { AlertCircle, ArrowDownToLine, BookOpen, CheckCircle2, ExternalLink, Leaf, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { errorMessage, formatDate, request } from './api';
import { maimemoImportProblem, maimemoPreviewRemaining, maimemoWarning } from './maimemo';
import type { MaimemoImportResult, MaimemoPreview, MaimemoStatus } from './maimemo';
import './maimemo.css';

type Operation = '' | 'status' | 'preview' | 'import' | 'refresh';
const operationLabels: Record<Exclude<Operation, ''>, string> = {
  status: '正在检查本机墨墨配置…', preview: '正在读取墨墨词库，词库较大时需要稍候…',
  import: '正在将所选记录导入词间…', refresh: '正在刷新词库与学习记录…',
};
const count = (value: number) => value.toLocaleString('zh-CN');
function displayDate(value: string): string {
  // formatDate owns the shared presentation and UTC handling; guard malformed dates.
  try { return formatDate(value); } catch { return '时间不可用'; }
}

export default function MaimemoPanel({ disabled, onImported, onBusyChange }: {
  disabled: boolean; onImported: () => Promise<void>; onBusyChange?: (busy: boolean) => void;
}) {
  const [status, setStatus] = useState<MaimemoStatus | null>(null);
  const [statusError, setStatusError] = useState('');
  const [preview, setPreview] = useState<MaimemoPreview | null>(null);
  const [includePlanned, setIncludePlanned] = useState(false);
  const [expired, setExpired] = useState(false);
  const [operation, setOperation] = useState<Operation>('');
  const [error, setError] = useState('');
  const [outcome, setOutcome] = useState<MaimemoImportResult | null>(null);
  const [refreshError, setRefreshError] = useState('');
  const pending = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const onBusyChangeRef = useRef(onBusyChange);
  onBusyChangeRef.current = onBusyChange;
  const id = useId();
  const busy = disabled || Boolean(operation);

  const run = useCallback(async (name: Exclude<Operation, ''>, action: (signal: AbortSignal) => Promise<void>, onFailure?: (message: string) => void) => {
    if (!mounted.current || pending.current) return;
    const current = new AbortController();
    controller.current = current; pending.current = true;
    setOperation(name); onBusyChangeRef.current?.(true);
    try { await action(current.signal); }
    catch (cause) {
      if (mounted.current && !current.signal.aborted && controller.current === current) (onFailure ?? setError)(errorMessage(cause));
    } finally {
      // An aborted StrictMode setup must not release a newer setup's operation.
      if (controller.current === current) {
        controller.current = null; pending.current = false;
        if (mounted.current) setOperation('');
        onBusyChangeRef.current?.(false);
      }
    }
  }, []);

  const loadStatus = useCallback(() => run('status', async signal => {
    setStatusError('');
    const value = await request<MaimemoStatus>('/integrations/maimemo/status', { signal });
    if (!signal.aborted) setStatus(value);
  }, setStatusError), [run]);

  useEffect(() => {
    mounted.current = true;
    void loadStatus();
    return () => {
      mounted.current = false;
      controller.current?.abort(); controller.current = null; pending.current = false;
      onBusyChangeRef.current?.(false);
    };
  }, [loadStatus]);

  useEffect(() => {
    if (!preview) { setExpired(false); return; }
    const remaining = maimemoPreviewRemaining(preview);
    setExpired(remaining <= 0);
    if (remaining > 0) {
      const timer = setTimeout(() => setExpired(true), Math.min(remaining, 2_147_483_647));
      return () => clearTimeout(timer);
    }
  }, [preview]);

  const readPreview = () => {
    if (disabled || pending.current || !status?.configured) return;
    void run('preview', async signal => {
      setError(''); setPreview(null); setIncludePlanned(false);
      const value = await request<MaimemoPreview>('/integrations/maimemo/preview', { method: 'POST', signal });
      if (!signal.aborted) { setPreview(value); setOutcome(null); setRefreshError(''); }
    });
  };

  const importPreview = () => {
    if (disabled || pending.current) return;
    const problem = maimemoImportProblem(preview, includePlanned);
    if (problem || !preview) { setError(problem); return; }
    const selected = preview;
    void run('import', async signal => {
      setError('');
      const value = await request<MaimemoImportResult>('/integrations/maimemo/import', {
        method: 'POST', body: JSON.stringify({ preview_id: selected.preview_id, include_planned: includePlanned }), signal,
      });
      if (signal.aborted) return;
      setOutcome(value); setPreview(null); setRefreshError(''); setOperation('refresh');
      // The write has succeeded. Refresh failures must not become import failures.
      const [learning, connection] = await Promise.allSettled([
        Promise.resolve().then(onImported),
        request<MaimemoStatus>('/integrations/maimemo/status', { signal }),
      ]);
      if (signal.aborted) return;
      if (learning.status === 'rejected') setRefreshError(errorMessage(learning.reason));
      if (connection.status === 'fulfilled') { setStatus(connection.value); setStatusError(''); }
      else setStatusError(`导入已完成，连接状态刷新失败：${errorMessage(connection.reason)}`);
    });
  };

  const refreshList = () => {
    if (disabled || pending.current) return;
    void run('refresh', async signal => {
      await onImported();
      if (!signal.aborted) setRefreshError('');
    }, setRefreshError);
  };

  const importProblem = preview ? maimemoImportProblem(preview, includePlanned) : '';
  const previewWarning = preview ? maimemoWarning(preview) : '';
  const outcomeWarning = outcome ? maimemoWarning(outcome) : '';

  return <section className="panel maimemo-panel" aria-labelledby={`${id}-title`} aria-busy={Boolean(operation)}>
    <div className="maimemo-heading">
      <span className="maimemo-icon"><BookOpen size={25} aria-hidden="true" /></span>
      <div><span className="maimemo-eyebrow">墨墨 × 词间</span><h2 id={`${id}-title`}>把背过的词，带回阅读里</h2><p>从墨墨读取词库与学习经历，在词间的上下文里继续积累。</p></div>
      <span className="maimemo-local"><ShieldCheck size={14} aria-hidden="true" />本机只读导入</span>
    </div>

    <div className="maimemo-body">
      <div className="maimemo-intro-note"><Leaf size={18} aria-hidden="true" /><p>只读取墨墨，不修改墨墨数据。词间已有的自评与复习排期会保留；墨墨的“认识”不代表词间已掌握，新词需在词间自评后才会进入复习。</p></div>

      {status && <div className="maimemo-connection">
        <span className={`maimemo-connection-state${status.configured ? ' connected' : ''}`}><i aria-hidden="true" />{status.configured ? '本机已配置墨墨连接' : '尚未配置墨墨连接'}</span>
        <span>已关联 {count(status.imported_words)} 个词元<span className="maimemo-status-divider">·</span>{status.last_synced_at ? `上次导入 ${displayDate(status.last_synced_at)}` : '尚未导入'}</span>
      </div>}

      {statusError && <div className="maimemo-error" role="alert"><AlertCircle size={18} aria-hidden="true" /><div><p>{statusError}</p><button type="button" className="text-button" disabled={busy} onClick={() => void loadStatus()}><RefreshCw size={14} aria-hidden="true" />重新检查连接</button></div></div>}

      {status && !status.configured && <details className="maimemo-setup">
        <summary>如何连接我的墨墨词库</summary>
        <div><p>在本机项目的 <code>.env</code> 中设置 <code>MAIMEMO_API_TOKEN</code>，保存后重启词间后端。令牌仅由本机后端读取，浏览器无需填写。</p><p>令牌获取与权限说明请查看<a href="https://www.maimemo.com/pages/openapi/introduction?open_by=share" target="_blank" rel="noopener noreferrer">墨墨官方开放 API 说明<ExternalLink size={12} aria-hidden="true" /></a>。</p><button type="button" className="button secondary" disabled={busy} onClick={() => void loadStatus()}><RefreshCw size={15} aria-hidden="true" />已配置，重新检查</button></div>
      </details>}

      {status?.configured && <div className="maimemo-read-actions"><button type="button" className="button secondary" disabled={busy} onClick={readPreview}>{operation === 'preview' ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />}{preview || outcome ? '重新读取墨墨词库' : '读取墨墨词库'}</button><p>先读取并核对，点击确认后才会写入词间。预览保留 5 分钟。</p></div>}

      {operation && <p className="maimemo-progress" role="status" aria-live="polite"><LoaderCircle size={16} className="spin" aria-hidden="true" />{operationLabels[operation]}</p>}
      {error && <div className="maimemo-error" role="alert"><AlertCircle size={18} aria-hidden="true" /><p>{error}</p></div>}

      {preview && <div className="maimemo-preview" aria-labelledby={`${id}-preview`}>
        <div className="maimemo-preview-heading"><h3 id={`${id}-preview`}>核对这次读取的词库</h3><span className={`maimemo-fetch-badge${preview.complete ? '' : ' partial'}`}>{preview.complete ? '已完整读取' : '仅获取部分'}</span></div>
        <p className="maimemo-remote-total">{preview.remote_total === null ? '墨墨未返回词库总记录数。' : `墨墨报告共 ${count(preview.remote_total)} 条记录。`}以下统计仅针对本次已读取内容。</p>
        <dl className="maimemo-counts"><div><dt>已读取记录</dt><dd>{count(preview.fetched_count)}<span>条</span></dd></div><div><dt>有学习经历</dt><dd>{count(preview.studied_count)}<span>条记录</span></dd></div><div><dt>未学习规划</dt><dd>{count(preview.planned_count)}<span>条记录</span></dd></div><div><dt>去重后有效词汇</dt><dd>{count(preview.valid_words)}<span>个词元</span></dd></div></dl>
        <p className="maimemo-count-note">有效词汇按词元归并；学习经历与规划数量按原始记录统计，并非去重词数。{preview.invalid_words > 0 && `本次读取还有 ${count(preview.invalid_words)} 条无效记录，将按所选范围跳过。`}</p>
        {previewWarning && <div className="maimemo-warning" role="status"><AlertCircle size={17} aria-hidden="true" /><p>{previewWarning}</p></div>}
        {preview.sample_words.length > 0 && <div className="maimemo-samples"><span>已读取词汇示例 · 全部读取范围</span><ul aria-label="墨墨词汇示例">{preview.sample_words.map((word, index) => <li key={`${index}-${word}`}>{word}</li>)}</ul></div>}
        <div className="maimemo-selection"><label><input type="checkbox" checked={includePlanned} disabled={busy || expired} onChange={event => setIncludePlanned(event.target.checked)} /><span>同时导入未学习的规划词<small>默认仅导入有学习经历的记录</small></span></label><p>当前范围：{includePlanned ? '有学习经历的记录，以及尚未学习的规划记录。' : '仅有学习经历的记录。'}实际新增词数在导入后显示。</p></div>
        {importProblem && <p className="maimemo-blocked" role="status">{importProblem}</p>}
        <div className="maimemo-confirm-actions"><button type="button" className="button primary" disabled={busy || expired || Boolean(importProblem)} onClick={importPreview}><ArrowDownToLine size={16} aria-hidden="true" />{preview.complete ? '确认导入词间' : '确认导入已读取的部分'}</button><p>有效至 {displayDate(preview.expires_at)}{expired && ' · 请重新读取'}</p></div>
      </div>}

      {outcome && <div className="maimemo-result" role="status" aria-live="polite"><div className="maimemo-result-title"><CheckCircle2 size={23} aria-hidden="true" /><h3>墨墨词汇已导入词间</h3></div><p>新增 <strong>{count(outcome.added)}</strong> 个词元，已有 <strong>{count(outcome.existing)}</strong> 个词元；更新了 {count(outcome.source_updated)} 个词元的墨墨来源记录。</p><p className="maimemo-result-detail">处理 {count(outcome.imported_records)} 条有效记录，归并 {count(outcome.duplicate_records)} 条重复词元记录，跳过 {count(outcome.invalid_words)} 条无效记录。新词等待你在词间自评。</p>{outcomeWarning && <div className="maimemo-warning"><AlertCircle size={17} aria-hidden="true" /><p>{outcomeWarning}</p></div>}</div>}
      {refreshError && <div className="maimemo-error" role="alert"><AlertCircle size={18} aria-hidden="true" /><div><strong>导入已完成，列表刷新失败</strong><p>{refreshError}</p><button type="button" className="button secondary" disabled={busy} onClick={refreshList}><RefreshCw size={15} aria-hidden="true" />只刷新列表</button></div></div>}
    </div>
  </section>;
}
