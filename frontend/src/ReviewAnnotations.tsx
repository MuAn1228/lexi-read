import { ArrowDown, LoaderCircle, NotebookPen, RefreshCw, Sparkles, Square } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage, request } from './api';
import { dictionaryAnnotationsPath, dictionaryPath } from './dictionary';
import type { DictionaryResult } from './dictionary';
import { mergeReviewAnnotations, needsReviewAnnotations, needsReviewLesson } from './reviewAnnotationState';
import WordAnnotations from './WordAnnotations';

/** Prefetch local definitions only; personal API reads wait until the learner reveals. */
export default function ReviewAnnotations({ word, revealed, active, disabled, summary, autoExplain, onAutoExplainChange, onGoToRating }: {
  word: string; revealed: boolean; active: boolean; disabled: boolean; summary?: string | null;
  autoExplain: boolean; onAutoExplainChange: (value: boolean) => void;
  onGoToRating: () => void;
}) {
  const [result, setResult] = useState<DictionaryResult | null>(null);
  const [reading, setReading] = useState(false);
  const [readError, setReadError] = useState('');
  const [operationError, setOperationError] = useState('');
  const [pending, setPending] = useState<'maimemo' | 'detail' | null>(null);
  const [retry, setRetry] = useState(0);
  const epoch = useRef(0);
  const operation = useRef<AbortController | null>(null);
  const attempted = useRef(false);
  const detailAttempted = useRef(false);

  useEffect(() => {
    const scope = ++epoch.current;
    operation.current?.abort(); operation.current = null;
    setPending(null); setOperationError(''); attempted.current = false;
    detailAttempted.current = false;
    if (!active) return;
    const controller = new AbortController();
    setReading(true); setReadError(''); setResult(null);
    request<DictionaryResult>(dictionaryPath(word), { signal: controller.signal })
      .then(value => { if (scope === epoch.current && !controller.signal.aborted) setResult(value); })
      .catch(reason => { if (scope === epoch.current && !controller.signal.aborted) setReadError(errorMessage(reason)); })
      .finally(() => { if (scope === epoch.current && !controller.signal.aborted) setReading(false); });
    return () => {
      ++epoch.current; controller.abort(); operation.current?.abort(); operation.current = null;
    };
  }, [word, active, retry]);

  const fetchContent = useCallback(async (kind: 'maimemo' | 'detail', refresh = false) => {
    if (!active || disabled || !revealed || operation.current) return;
    const controller = new AbortController(); operation.current = controller;
    const scope = epoch.current;
    setPending(kind); setOperationError('');
    try {
      const value = await request<DictionaryResult>(kind === 'maimemo' ? dictionaryAnnotationsPath(word) : dictionaryPath(word, true), {
        method: 'POST', ...(kind === 'maimemo' ? { body: JSON.stringify({ refresh }) } : {}), signal: controller.signal,
      });
      if (scope === epoch.current && !controller.signal.aborted) setResult(current => mergeReviewAnnotations(current, value));
    } catch (reason) {
      if (scope === epoch.current && !controller.signal.aborted) setOperationError(errorMessage(reason));
    } finally {
      if (scope === epoch.current && operation.current === controller) {
        operation.current = null; setPending(null);
      }
    }
  }, [active, disabled, revealed, word]);

  useEffect(() => {
    if (active && revealed && !disabled && result && needsReviewAnnotations(result) && !attempted.current && !operation.current) {
      attempted.current = true;
      void fetchContent('maimemo');
    }
  }, [active, revealed, disabled, result, fetchContent]);

  useEffect(() => {
    const personalReady = result && (!result.can_fetch_annotations || Boolean(result.maimemo) || attempted.current);
    if (active && revealed && !disabled && autoExplain && result && personalReady && !pending && !operation.current && !detailAttempted.current && needsReviewLesson(result)) {
      detailAttempted.current = true;
      void fetchContent('detail');
    }
  }, [active, revealed, disabled, autoExplain, result, pending, fetchContent]);

  const stop = () => {
    detailAttempted.current = true;
    operation.current?.abort(); operation.current = null; setPending(null);
  };

  if (!revealed) return null;
  const annotationAction = result?.can_fetch_annotations ? <div className="review-annotation-actions">
    {pending === 'maimemo' ? <><p role="status"><LoaderCircle size={15} className="spin" />正在读取墨墨个人注解，本地释义可先查看。</p><button type="button" className="text-button" onClick={stop}><Square size={13} />停止等待</button></> : <button type="button" className="text-button" disabled={disabled || Boolean(pending)} onClick={() => void fetchContent('maimemo', Boolean(result.maimemo))}><NotebookPen size={14} />{result.maimemo ? '更新墨墨个人注解' : '读取墨墨个人注解'}</button>}
  </div> : undefined;
  const detailAction = <div className="review-annotation-detail-action">
    <p>补充逐项中文讲解、双语例句、常用搭配、用法提示和易混词辨析。成功后自动保存在本机，下次直接查看。</p>
    {pending === 'detail' ? <div className="review-annotation-actions"><p role="status"><LoaderCircle size={15} className="spin" />Agnes 正在整理详细讲解，可先按上面的词义自评。</p><button type="button" className="text-button" onClick={stop}><Square size={13} />停止等待</button></div> : <button type="button" className="button secondary" disabled={disabled || Boolean(pending) || !result?.can_generate} onClick={() => void fetchContent('detail')}><Sparkles size={15} />{result?.can_generate ? '补充详细例句与用法' : 'AI 详细讲解暂不可用'}</button>}
  </div>;

  return <section className="review-annotations" aria-label="单词详细注解">
    <div className="review-annotation-preference"><label><input type="checkbox" checked={autoExplain} onChange={event => { const checked = event.target.checked; if (checked) detailAttempted.current = false; onAutoExplainChange(checked); if (!checked && pending === 'detail') stop(); }} />自动补充详细讲解</label><span>揭晓后优先读取墨墨个人注解；不足时由 Agnes 补充，仅处理当前词。</span><button type="button" className="text-button" onClick={onGoToRating}><ArrowDown size={14} />前往自评</button></div>
    {reading && <p className="loading-copy" role="status"><LoaderCircle size={16} className="spin" />正在读取本地词典…</p>}
    {readError && <div className="inline-error" role="alert"><p>{readError}</p><button type="button" className="text-button" disabled={disabled || reading} onClick={() => setRetry(value => value + 1)}><RefreshCw size={14} />重新读取注解</button></div>}
    {summary && !result?.dictionary && !result?.detail && <div className="review-annotation-summary"><p className="section-label">已有简释</p><p>{summary}</p></div>}
    {operationError && <p className="inline-error" role="alert">{operationError} 本地词义和当前答案已保留，可重试。</p>}
    {result && <WordAnnotations result={result} compact showPhonetic annotationAction={annotationAction} detailAction={detailAction} />}
  </section>;
}
