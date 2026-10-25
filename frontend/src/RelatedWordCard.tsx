import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Check, Eye, LoaderCircle, RefreshCw, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import { errorMessage, request } from './api';
import type { RecommendationAction, RecommendationFeedback, RelatedWord, RelationType, WordStatus } from './types';
import PronunciationButton from './PronunciationButton';
import { useMascot } from './mascot/useMascot';

const relationLabels: Record<RelationType, string> = {
  word_family: '同词族', synonym: '近义词', antonym: '反义词',
  concept: '概念关联', collocation: '常见搭配', domain_related: '同领域',
};
type Attempt = { action: RecommendationAction; request_id: string };
type FailedAttempt = Attempt & { message: string };

export default function RelatedWordCard({ item, batchId, status, disabled, onSaved, variant = 'recommendation', motionIndex }: {
  item: RelatedWord; batchId: number; status: WordStatus | null; disabled: boolean;
  onSaved: () => Promise<void>;
  variant?: 'recommendation' | 'expansion';
  motionIndex?: number;
}) {
  const { emit } = useMascot();
  const card = useRef<HTMLElement>(null);
  const reason = useRef<HTMLParagraphElement>(null);
  const disclosureId = useId();
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
  useEffect(() => {
    // The trigger folds away after explicit viewing; keep keyboard focus in the
    // revealed content, without scrolling the document or recording another view.
    if (expanded) reason.current?.focus({ preventScroll: true });
  }, [expanded]);

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
      // Impressions and opening a reason are observations, not saved learning choices.
      if (attempt.action !== 'SHOWN' && attempt.action !== 'VIEWED') {
        emit({ type: 'FEEDBACK_SAVED', requestId: attempt.request_id, word: item.word });
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
  }, [batchId, item.word, refresh, emit]);

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
  const savingLabel = busy === 'VIEWED' ? '正在保存查看记录…' : busy === 'KNOWN' || busy === 'UNKNOWN' ? '正在保存掌握状态…' : '正在保存反馈…';
  return <article className={`related-card${motionIndex === undefined ? '' : ' related-card-enter'}`} style={motionIndex === undefined ? undefined : { '--related-reveal-order': Math.min(2, Math.max(0, motionIndex)) } as CSSProperties} ref={card} aria-label={`${item.word} 关联推荐`}>
    {variant === 'expansion' ? <div className="related-card-heading"><h4>{item.word}</h4><PronunciationButton text={item.word} label={`朗读 ${item.word}`} compact /></div> : <h4>{item.word}</h4>}
    <p className="related-meaning">{item.meaning}</p>
    <div className="related-priority"><span className="tiny-badge">{variant === 'expansion' && item.relation === 'word_family' ? '派生词' : variant === 'expansion' && item.relation === 'domain_related' ? '同类词' : relationLabels[item.relation]}</span>{variant === 'recommendation' && Number.isFinite(item.priority) && <span>学习优先级 {Math.round(item.priority * 100)} / 100</span>}</div>
    <div className="related-disclosure-trigger" data-expanded={expanded} aria-hidden={expanded} inert={expanded}><div>
      <button className="text-button related-view" disabled={blocked} aria-expanded={expanded} aria-controls={disclosureId} onClick={() => choose('VIEWED')}><Eye size={14} />{variant === 'expansion' ? '查看关联与区别' : '查看关联'}</button>
    </div></div>
    <div id={disclosureId} className="related-disclosure" data-expanded={expanded} aria-hidden={!expanded} inert={!expanded}><div className="related-disclosure-inner">
      <p className="related-reason" ref={reason} tabIndex={-1}>{item.reason}</p>
      <fieldset className="related-feedback" disabled={blocked}><legend>你认识这个词吗？</legend>
        <button type="button" className={currentStatus === 'KNOWN' ? 'selected' : ''} aria-pressed={currentStatus === 'KNOWN'} aria-busy={busy === 'KNOWN'} disabled={variant === 'expansion' && currentStatus === 'KNOWN'} onClick={() => choose('KNOWN')}><Check size={13} />{variant === 'expansion' ? '已掌握' : '认识'}</button>
        <button type="button" className={currentStatus === 'UNKNOWN' ? 'selected' : ''} aria-pressed={currentStatus === 'UNKNOWN'} aria-busy={busy === 'UNKNOWN'} disabled={variant === 'expansion' && (currentStatus === 'UNKNOWN' || currentStatus === 'KNOWN')} onClick={() => choose('UNKNOWN')}><X size={13} />{variant === 'expansion' ? currentStatus === 'KNOWN' ? '已在词库 · 已掌握' : currentStatus === 'UNKNOWN' ? '已在生词库' : '加入生词' : '不认识'}</button>
      </fieldset>
      <fieldset className="related-feedback" disabled={blocked}><legend>这个关联对你有帮助吗？</legend>
        <button className={feedback?.useful === true ? 'selected' : ''} aria-pressed={feedback?.useful === true} aria-busy={busy === 'USEFUL'} onClick={() => choose('USEFUL')}><ThumbsUp size={13} />这个关联有用</button>
        <button className={feedback?.useful === false ? 'selected' : ''} aria-pressed={feedback?.useful === false} aria-busy={busy === 'NOT_USEFUL'} onClick={() => choose('NOT_USEFUL')}><ThumbsDown size={13} />这个关联没用</button>
      </fieldset>
    </div></div>
    {busy && <p className="feedback-saving" role="status"><LoaderCircle size={12} className="spin" />{savingLabel}</p>}
    {failure && <div className="inline-error feedback-error" role="alert"><p>反馈保存失败：{failure.message}</p><button className="text-button" disabled={busy !== null} onClick={() => void send(failure)}><RefreshCw size={13} />重试保存反馈</button></div>}
    {shownFailure && <div className="inline-error feedback-error" role="alert"><p>展示记录保存失败：{shownFailure.message}</p><button className="text-button" disabled={shownBusy} onClick={() => void send(shownFailure)}><RefreshCw size={13} />重试保存展示记录</button></div>}
    {syncError && <div className="inline-error feedback-error" role="alert"><p>反馈已保存，学习记录刷新失败：{syncError}</p><button className="text-button" disabled={busy !== null} onClick={() => void refresh()}>刷新学习记录</button></div>}
  </article>;
}
