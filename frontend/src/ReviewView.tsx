import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, ArrowRight, BookOpen, Check, CircleHelp, Eye, LoaderCircle, RefreshCw, RotateCcw, SkipForward, X } from 'lucide-react';
import { errorMessage, formatDate, request } from './api';
import { StatusBadge } from './WordControls';
import type { ReviewBatch, ReviewItem, Vocabulary, WordStatus } from './types';

interface ReviewSession extends ReviewBatch {
  cursor: number; assessed: number; skipped: number; removed: number; size: number;
}
const ratings = [
  { status: 'KNOWN', label: '已掌握', description: '能想起它的意思', icon: Check },
  { status: 'UNCERTAIN', label: '不确定', description: '还有些模糊', icon: CircleHelp },
  { status: 'UNKNOWN', label: '不认识', description: '暂时想不起来', icon: X },
] as const;

export default function ReviewView({ active, vocabulary, disabled, onAssess, onOpenDocument }: {
  active: boolean; vocabulary: Vocabulary[]; disabled: boolean;
  onAssess: (item: ReviewItem, status: WordStatus, requestId: string) => Promise<boolean>;
  onOpenDocument: (documentId: number, occurrenceId: number) => void;
}) {
  const [session, setSession] = useState<ReviewSession | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [failedStatus, setFailedStatus] = useState<WordStatus | null>(null);
  const [showContext, setShowContext] = useState(false);
  const loadingRef = useRef(false);
  const savingRef = useRef(false);
  const pendingAssessment = useRef<{ vocabularyId: number; status: WordStatus; requestId: string } | null>(null);
  const wordHeading = useRef<HTMLHeadingElement>(null);
  const summaryHeading = useRef<HTMLHeadingElement>(null);
  const previousCard = useRef<number | null>(null);

  const loadBatch = useCallback(async () => {
    if (loadingRef.current || savingRef.current) return;
    loadingRef.current = true; setLoading(true); setLoadError('');
    try {
      const batch = await request<ReviewBatch>('/review?limit=10');
      const items = batch.items.filter(item => item.vocabulary.status === 'UNKNOWN' || item.vocabulary.status === 'UNCERTAIN');
      setSession({ ...batch, items, size: items.length, cursor: 0, assessed: 0, skipped: 0, removed: 0 });
      setShowContext(false); setSaveError(''); setFailedStatus(null);
      previousCard.current = null;
    } catch (error) { setLoadError(errorMessage(error)); }
    finally { loadingRef.current = false; setLoading(false); }
  }, []);

  useEffect(() => {
    if (active && !session && !loadingRef.current && !loadError) void loadBatch();
  }, [active, session, loadError, loadBatch]);

  // A word mastered in the reader leaves the remaining batch without becoming a review assessment.
  useEffect(() => {
    if (!active || saving || savingRef.current) return;
    const known = new Set(vocabulary.filter(word => word.status === 'KNOWN').map(word => word.lemma));
    setSession(current => {
      if (!current) return current;
      const remaining = current.items.slice(current.cursor).filter(item => !known.has(item.vocabulary.lemma));
      const removed = current.items.length - current.cursor - remaining.length;
      return removed ? { ...current, items: [...current.items.slice(0, current.cursor), ...remaining], removed: current.removed + removed } : current;
    });
  }, [active, vocabulary, saving]);

  const item = session?.items[session.cursor];
  const finished = Boolean(session && !item && session.size > 0);
  useEffect(() => {
    setShowContext(false); setSaveError(''); setFailedStatus(null);
    pendingAssessment.current = null;
  }, [item?.vocabulary.id]);
  useEffect(() => {
    if (!active || loading) return;
    if (item && previousCard.current !== item.vocabulary.id) {
      previousCard.current = item.vocabulary.id; wordHeading.current?.focus();
    } else if (finished) summaryHeading.current?.focus();
  }, [active, loading, item?.vocabulary.id, finished]);

  const assess = async (status: WordStatus) => {
    if (!item || savingRef.current || disabled || loadingRef.current) return;
    savingRef.current = true; setSaving(true); setSaveError(''); setFailedStatus(null);
    try {
      if (pendingAssessment.current?.vocabularyId !== item.vocabulary.id || pendingAssessment.current.status !== status) {
        pendingAssessment.current = { vocabularyId: item.vocabulary.id, status, requestId: crypto.randomUUID() };
      }
      if (!await onAssess(item, status, pendingAssessment.current.requestId)) {
        setFailedStatus(status);
        setSaveError('这次自评未能保存，仍停留在当前单词。请重试保存，或重新选择评价。');
        return;
      }
      setSession(current => current && current.items[current.cursor]?.vocabulary.id === item.vocabulary.id
        ? { ...current, cursor: current.cursor + 1, assessed: current.assessed + 1 } : current);
    } catch (error) { setFailedStatus(status); setSaveError(errorMessage(error)); }
    finally { savingRef.current = false; setSaving(false); }
  };
  const skip = () => {
    if (savingRef.current || disabled || loadingRef.current) return;
    setSession(current => current && current.items[current.cursor]
      ? { ...current, cursor: current.cursor + 1, skipped: current.skipped + 1 } : current);
  };
  const blocked = disabled || saving || loading;
  const processed = session ? session.assessed + session.skipped + session.removed : 0;
  const latestWord = vocabulary.find(word => word.lemma === item?.vocabulary.lemma) ?? item?.vocabulary;

  return <section className="panel review-panel" hidden={!active} aria-labelledby="review-title">
    <div className="library-heading review-heading"><div><h2 id="review-title"><RotateCcw size={19} />再见一次，记得更清楚。</h2><p>每组最多 10 个「不认识」或「不确定」的词，优先复习尚未复习或较久未复习的词。</p></div>{session && <span className="count-badge">本组 {session.size} 个词</span>}</div>
    {loading && <div className="review-message" role="status"><LoaderCircle size={23} className="spin" /><p>正在准备这一组单词…</p></div>}
    {loadError && <div className="review-message"><AlertCircle size={27} /><p role="alert">{loadError}</p><button className="button secondary" disabled={blocked} onClick={() => void loadBatch()}><RefreshCw size={15} />重新加载</button></div>}
    {!loading && !loadError && session && <>
      {session.size > 0 && <div className="review-progress"><div><span aria-live="polite">已处理 {processed} / {session.size}</span><span>已自评 {session.assessed} · 已跳过 {session.skipped}{session.removed > 0 && ` · 阅读中已掌握 ${session.removed}`}</span></div><div className="progress-track" role="progressbar" aria-label="本组复习进度" aria-valuemin={0} aria-valuemax={session.size} aria-valuenow={processed}><div style={{ width: `${processed / session.size * 100}%` }} /></div></div>}
      {item && <div className="review-card" aria-busy={saving}>
        <p className="overline">先回想这个词的意思，再给自己一个评价</p>
        <h3 className="review-word" ref={wordHeading} tabIndex={-1}>{item.vocabulary.word}</h3>
        {item.vocabulary.lemma !== item.vocabulary.word.toLowerCase() && <p className="review-lemma">词元 · {item.vocabulary.lemma}</p>}
        <div className="review-meta"><StatusBadge status={latestWord?.status ?? item.vocabulary.status} /><span>{item.last_reviewed_at ? <>最近复习 · <time dateTime={item.last_reviewed_at}>{formatDate(item.last_reviewed_at)}</time></> : '尚未在这里复习'}</span></div>
        <div className="review-context-actions"><button className="button secondary" disabled={blocked || !item.occurrence} aria-expanded={showContext} aria-controls="review-context" onClick={() => setShowContext(current => !current)}><Eye size={15} />{showContext ? '收起原文提示' : '显示原文提示'}</button>{item.occurrence && <button className="text-button" disabled={blocked} onClick={() => onOpenDocument(item.occurrence!.document_id, item.occurrence!.id)}><BookOpen size={15} />回到原文 <ArrowRight size={14} /></button>}</div>
        {item.occurrence ? <div id="review-context" className="review-context" hidden={!showContext}><p className="section-label">原文 · {item.occurrence.document_title}</p><blockquote>{item.occurrence.sentence || '该词暂无可用的原句，可回到原文查看。'}</blockquote></div> : <p className="review-context-empty">这个词暂时没有关联原文，可以根据记忆自评。</p>}
        <fieldset className="review-ratings" disabled={blocked}><legend>这次，你对它有多熟悉？</legend><div>{ratings.map(rating => <button className={`review-rating ${rating.status.toLowerCase()}`} key={rating.status} onClick={() => void assess(rating.status)}><rating.icon size={20} /><strong>{rating.label}</strong><span>{rating.description}</span></button>)}</div></fieldset>
        {saveError && <div className="review-save-error" role="alert"><p><AlertCircle size={16} />{saveError}</p>{failedStatus && <button className="text-button" disabled={blocked} onClick={() => void assess(failedStatus)}><RefreshCw size={14} />重试保存</button>}</div>}
        <div className="review-card-footer"><p>{saving ? <><LoaderCircle className="spin" size={14} />正在保存自评…</> : '自评会保存到词库，并记录本次复习。'}</p><button className="text-button" disabled={blocked} onClick={skip}><SkipForward size={15} />跳过这个词</button></div>
      </div>}
      {finished && <div className="review-message review-complete"><span className="review-complete-icon"><Check size={29} /></span><h3 ref={summaryHeading} tabIndex={-1}>这一组，已经走完了。</h3><p>已自评 <strong>{session.assessed}</strong> 个词 · 已跳过 <strong>{session.skipped}</strong> 个词</p>{session.removed > 0 && <p>另有 {session.removed} 个词已在阅读中标为掌握，未计入本次自评。</p>}<p>跳过的词保留原状态；仍不熟悉的词可能在后续组中再次出现。</p><button className="button primary" disabled={blocked} onClick={() => void loadBatch()}><RefreshCw size={16} />加载下一组</button></div>}
      {session.size === 0 && <div className="review-message"><BookOpen size={32} /><h3>暂时没有需要复习的生词</h3><p>阅读时把单词标为「不认识」或「不确定」，就能在这里再次遇见。</p><button className="button secondary" disabled={blocked} onClick={() => void loadBatch()}><RefreshCw size={15} />重新检查词库</button></div>}
    </>}
    <div className="review-note">先回想，再看原文提示。跳过不会修改状态或掌握度。</div>
  </section>;
}
