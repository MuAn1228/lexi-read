import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Eye, LoaderCircle, RefreshCw, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import { errorMessage, request } from './api';
import type { RecommendationAction, RecommendationFeedback, RelatedWord, RelationType, WordStatus } from './types';

const relationLabels: Record<RelationType, string> = {
  word_family: '同词族', synonym: '近义词', antonym: '反义词',
  concept: '概念关联', collocation: '常见搭配', domain_related: '同领域',
};
type Attempt = { action: RecommendationAction; request_id: string };
type FailedAttempt = Attempt & { message: string };

export default function RelatedWordCard({ item, batchId, status, disabled, onSaved }: {
  item: RelatedWord; batchId: number; status: WordStatus | null; disabled: boolean;
  onSaved: () => Promise<void>;
}) {
  const card = useRef<HTMLElement>(null);
  const active = useRef(false);
  const shownStarted = useRef(false);
  const savingAction = useRef(false);
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState<RecommendationFeedback | null>(null);
  const [busy, setBusy] = useState<RecommendationAction | null>(null);
  const [shownBusy, setShownBusy] = useState(false);
  const [failure, setFailure] = useState<FailedAttempt | null>(null);
  const [shownFailure, setShownFailure] = useState<FailedAttempt | null>(null);
  const [syncError, setSyncError] = useState('');

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  const refresh = useCallback(async () => {
    try { await onSaved(); if (active.current) setSyncError(''); }
    catch (error) { if (active.current) setSyncError(errorMessage(error)); }
  }, [onSaved]);

  const send = useCallback(async (attempt: Attempt) => {
    const passive = attempt.action === 'SHOWN';
    if (!passive && savingAction.current) return;
    if (passive) setShownBusy(true);
    else { savingAction.current = true; setBusy(attempt.action); }
    try {
      const result = await request<RecommendationFeedback>(`/recommendations/${batchId}/feedback`, {
        method: 'POST', body: JSON.stringify({ word: item.word, action: attempt.action, request_id: attempt.request_id }),
      });
      if (active.current) {
        if (passive) setShownFailure(null);
        else { setFeedback(result); setFailure(null); }
      }
      // A response to a passive impression must not overwrite a newer deliberate choice.
      // Complete global refresh even if navigation has unmounted this card.
      await refresh();
    } catch (error) {
      if (active.current) {
        const failed = { ...attempt, message: errorMessage(error) };
        if (passive) setShownFailure(failed); else setFailure(failed);
      }
    } finally {
      if (!passive) savingAction.current = false;
      if (active.current) { if (passive) setShownBusy(false); else setBusy(null); }
    }
  }, [batchId, item.word, refresh]);

  useEffect(() => {
    if (!card.current) return;
    const observer = new IntersectionObserver(entries => {
      if (shownStarted.current || !entries.some(entry => entry.isIntersecting && entry.intersectionRatio >= 0.25)) return;
      shownStarted.current = true;
      observer.disconnect();
      void send({ action: 'SHOWN', request_id: crypto.randomUUID() });
    }, { threshold: 0.25 });
    observer.observe(card.current);
    return () => observer.disconnect();
  }, [send]);

  const choose = (action: RecommendationAction) => {
    if (disabled || savingAction.current || failure) return;
    if (action === 'VIEWED') setExpanded(true);
    void send({ action, request_id: crypto.randomUUID() });
  };
  const blocked = disabled || busy !== null || failure !== null;
  const currentStatus = status ?? feedback?.status;
  return <article className="related-card" ref={card} aria-label={`${item.word} 关联推荐`}>
    <h4>{item.word}</h4>
    <p className="related-meaning">{item.meaning}</p>
    <div className="related-priority"><span className="tiny-badge">{relationLabels[item.relation]}</span>{Number.isFinite(item.priority) && <span>学习优先级 {Math.round(item.priority * 100)} / 100</span>}</div>
    {!expanded ? <button className="text-button related-view" disabled={blocked} onClick={() => choose('VIEWED')}><Eye size={14} />查看关联</button> : <>
      <p className="related-reason">{item.reason}</p>
      <fieldset className="related-feedback" disabled={blocked}><legend>你认识这个词吗？</legend>
        <button className={currentStatus === 'KNOWN' ? 'selected' : ''} aria-pressed={currentStatus === 'KNOWN'} onClick={() => choose('KNOWN')}><Check size={13} />认识</button>
        <button className={currentStatus === 'UNKNOWN' ? 'selected' : ''} aria-pressed={currentStatus === 'UNKNOWN'} onClick={() => choose('UNKNOWN')}><X size={13} />不认识</button>
      </fieldset>
      <fieldset className="related-feedback" disabled={blocked}><legend>这个关联对你有帮助吗？</legend>
        <button className={feedback?.useful === true ? 'selected' : ''} aria-pressed={feedback?.useful === true} onClick={() => choose('USEFUL')}><ThumbsUp size={13} />这个关联有用</button>
        <button className={feedback?.useful === false ? 'selected' : ''} aria-pressed={feedback?.useful === false} onClick={() => choose('NOT_USEFUL')}><ThumbsDown size={13} />这个关联没用</button>
      </fieldset>
    </>}
    {busy && <p className="feedback-saving" role="status"><LoaderCircle size={12} className="spin" />正在保存反馈…</p>}
    {failure && <div className="inline-error feedback-error" role="alert"><p>反馈保存失败：{failure.message}</p><button className="text-button" disabled={busy !== null} onClick={() => void send(failure)}><RefreshCw size={13} />重试保存反馈</button></div>}
    {shownFailure && <div className="inline-error feedback-error" role="alert"><p>展示记录保存失败：{shownFailure.message}</p><button className="text-button" disabled={shownBusy} onClick={() => void send(shownFailure)}><RefreshCw size={13} />重试保存展示记录</button></div>}
    {syncError && <div className="inline-error feedback-error" role="alert"><p>反馈已保存，学习记录刷新失败：{syncError}</p><button className="text-button" disabled={busy !== null} onClick={() => void refresh()}>刷新学习记录</button></div>}
  </article>;
}
