import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, ArrowRight, BookOpen, Check, CircleHelp, Clock3, LoaderCircle, RefreshCw, RotateCcw, SkipForward, X } from 'lucide-react';
import { ApiError, errorMessage, formatDate, request, reviewStorageKey } from './api';
import { finishReviewCard, intervalLabel, newReviewSession, readReviewSession, reconcileReviewSession, reviewQuery, writeReviewSession } from './reviewStorage';
import type { ReviewSession } from './reviewStorage';
import { StatusBadge } from './WordControls';
import { PulseHeart } from './components/StudyInteractions';
import ReviewPractice from './ReviewPractice';
import { reviewCloze, reviewPracticeKey } from './reviewExercise';
import type { ReviewMode } from './reviewExercise';
import type { ReviewBatch, ReviewItem, ReviewSummary, Vocabulary, WordStatus } from './types';

const ratings = [
  { status: 'KNOWN', label: '已掌握', description: '能想起它的意思', icon: Check },
  { status: 'UNCERTAIN', label: '不确定', description: '还有些模糊', icon: CircleHelp },
  { status: 'UNKNOWN', label: '不认识', description: '暂时想不起来', icon: X },
] as const;

export default function ReviewView({ active, readerActive, vocabulary, disabled, onAssess, onOpenDocument, onStart }: {
  active: boolean; readerActive: boolean; vocabulary: Vocabulary[]; disabled: boolean;
  onAssess: (item: ReviewItem, status: WordStatus, requestId: string) => Promise<void>;
  onOpenDocument: (documentId: number, occurrenceId: number) => void;
  onStart: () => void;
}) {
  const [session, setSession] = useState<ReviewSession | null>(null);
  const [summary, setSummary] = useState<ReviewSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [storageWarning, setStorageWarning] = useState('');
  const [restored, setRestored] = useState(false);
  const [sessionVerified, setSessionVerified] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [failedStatus, setFailedStatus] = useState<WordStatus | null>(null);
  const [canDismiss, setCanDismiss] = useState(false);
  const [mode, setMode] = useState<ReviewMode>('recall');
  const [revealedCard, setRevealedCard] = useState('');
  const [confirmedAssessments, setConfirmedAssessments] = useState(0);
  const sessionRef = useRef<ReviewSession | null>(null);
  const storageKey = useRef('');
  const restoreAttempted = useRef(false);
  const loadingRef = useRef(false);
  const savingRef = useRef(false);
  const activeRef = useRef(active);
  const enabledRef = useRef(false);
  const lastCheck = useRef(0);
  const wordHeading = useRef<HTMLHeadingElement>(null);
  const summaryHeading = useRef<HTMLHeadingElement>(null);
  const previousCard = useRef<string | null>(null);
  activeRef.current = active;
  enabledRef.current = !disabled && (active || readerActive);

  // A pending request must be durable before the PUT starts.
  const commit = useCallback((value: ReviewSession, strict = false, afterWrite = false) => {
    try {
      if (!storageKey.current) throw new Error('浏览器复习进度尚未准备好，请重新检查后重试。');
      writeReviewSession(storageKey.current, value);
      if (strict) setStorageWarning('');
    } catch (error) {
      if (strict) throw error;
      setStorageWarning(afterWrite ? '自评已保存，但浏览器进度未能更新。刷新后可能需要重试确认这张卡，学习记录不会重复计入。' : errorMessage(error));
    }
    sessionRef.current = value;
    setSession(value);
  }, []);

  const refresh = useCallback(async (next = false) => {
    if (loadingRef.current || savingRef.current) return;
    loadingRef.current = true; setLoading(true); setLoadError('');
    try {
      if (activeRef.current && !restoreAttempted.current) {
        storageKey.current = await reviewStorageKey();
        const saved = readReviewSession(storageKey.current);
        setStorageWarning(saved.warning);
        if (saved.session) {
          sessionRef.current = saved.session;
          setSession(saved.session);
          setRestored(saved.session.cursor < saved.session.items.length);
        }
        restoreAttempted.current = true;
      }
      const current = sessionRef.current;
      const ids = activeRef.current && current && !next ? current.items.slice(current.cursor).map(item => item.vocabulary.id) : undefined;
      const batch = await request<ReviewBatch>(reviewQuery(ids));
      lastCheck.current = Date.now();
      setSummary(batch);
      if (activeRef.current && restoreAttempted.current) {
        commit(next || !current ? newReviewSession(batch.items) : reconcileReviewSession(current, batch.items));
        setSessionVerified(true);
        if (next) { setRestored(false); setRevealedCard(''); previousCard.current = null; }
      }
    } catch (error) { setLoadError(errorMessage(error)); }
    finally { loadingRef.current = false; setLoading(false); }
  }, [commit]);

  useEffect(() => {
    if (enabledRef.current) void refresh();
  }, [active, readerActive, disabled, vocabulary, refresh]);
  useEffect(() => {
    if (active && !disabled && !loading && !loadError && !restoreAttempted.current) void refresh();
  }, [active, disabled, loading, loadError, refresh]);

  // Check on return to the browser and near due times/local midnight, without second polling.
  useEffect(() => {
    if (!enabledRef.current) return;
    const midnight = new Date(); midnight.setHours(24, 0, 0, 100);
    const due = summary?.next_due_at ? Date.parse(/[zZ]$|[+-]\d{2}:\d{2}$/.test(summary.next_due_at) ? summary.next_due_at : `${summary.next_due_at}Z`) : Infinity;
    const delay = Math.max(15_000, Math.min(60_000, midnight.getTime() - Date.now(), due - Date.now() + 100));
    const timer = window.setTimeout(() => { if (window.document.visibilityState !== 'hidden') void refresh(); }, delay);
    const onFocus = () => { if (window.document.visibilityState !== 'hidden' && Date.now() - lastCheck.current > 15_000) void refresh(); };
    window.addEventListener('focus', onFocus);
    window.document.addEventListener('visibilitychange', onFocus);
    return () => { clearTimeout(timer); window.removeEventListener('focus', onFocus); window.document.removeEventListener('visibilitychange', onFocus); };
  }, [active, readerActive, disabled, summary, loading, refresh]);

  const item = session?.items[session.cursor];
  const practiceKey = item ? reviewPracticeKey(item.vocabulary.id, item.occurrence, mode) : '';
  const needsReveal = mode === 'cloze' && Boolean(reviewCloze(item?.occurrence ?? null)) && revealedCard !== practiceKey;
  const pending = session?.pending;
  const finished = Boolean(session && !item && session.size > 0);
  useEffect(() => {
    setSaveError(''); setFailedStatus(null); setCanDismiss(false);
  }, [item?.vocabulary.id]);
  useEffect(() => {
    if (!active || loading) return;
    if (item && previousCard.current !== practiceKey) {
      previousCard.current = practiceKey; wordHeading.current?.focus();
    } else if (finished) summaryHeading.current?.focus();
  }, [active, loading, practiceKey, finished]);

  const assess = async (status: WordStatus) => {
    const current = sessionRef.current;
    const card = current?.items[current.cursor];
    if (!current || !card || savingRef.current || disabled || loadingRef.current || (!sessionVerified && !current.pending) || (needsReveal && !current.pending)) return;
    if (current.pending && current.pending.status !== status) return;
    savingRef.current = true; setSaving(true); setSaveError(''); setCanDismiss(false); setFailedStatus(status);
    let confirmed = false;
    try {
      const attempt = current.pending ?? { item: card, status, requestId: crypto.randomUUID() };
      commit({ ...current, pending: attempt }, true);
      await onAssess(attempt.item, attempt.status, attempt.requestId);
      confirmed = true;
      commit(finishReviewCard(sessionRef.current!, 'assessed'), false, true);
      setConfirmedAssessments(value => value + 1);
      setFailedStatus(null);
    } catch (error) {
      setSaveError(`${errorMessage(error)} 当前单词仍保留，请重试确认本次自评。`);
      setCanDismiss(error instanceof ApiError && (error.status === 404 || error.status === 409));
    } finally {
      savingRef.current = false; setSaving(false);
      if (confirmed) void refresh();
    }
  };
  const skip = (dismiss = false) => {
    const current = sessionRef.current;
    if (!current || savingRef.current || disabled || loadingRef.current || (current.pending && !dismiss)) return;
    commit(finishReviewCard(current, dismiss ? 'removed' : 'skipped'));
  };
  const blocked = disabled || saving || loading;
  const processed = session ? session.assessed + session.skipped + session.removed : 0;
  const latestWord = vocabulary.find(word => word.lemma === item?.vocabulary.lemma) ?? item?.vocabulary;
  const stats = <div className="review-stats" aria-label="今日复习统计">
    <div><span>当前到期</span><strong data-review-stat="due">{summary?.total ?? '—'}</strong></div>
    <div><span>今日已复习</span><strong data-review-stat="reviewed">{summary?.reviewed_today ?? '—'}</strong></div>
    <div><span>今日稍后到期</span><strong data-review-stat="later">{summary?.later_today ?? '—'}</strong></div>
  </div>;
  const nextDue = summary?.next_due_at ? <span className="review-next-due">下次到期 · <time dateTime={summary.next_due_at}>{formatDate(summary.next_due_at)}</time></span> : null;

  return <>
    {readerActive && <section className="panel today-review" aria-labelledby="today-review-title">
      <div className="today-review-heading"><div><p className="overline">A LITTLE REVIEW, EVERY DAY</p><h2 id="today-review-title"><RotateCcw size={19} />今日待复习</h2><p>到期的生词与已掌握词，每次回顾一小组。</p></div><button className="button secondary" disabled={disabled} onClick={onStart}>{summary?.total === 0 ? '查看复习安排' : '开始今日复习'}<ArrowRight size={16} /></button></div>
      {stats}<div className="today-review-footer">{nextDue ?? <span>{summary?.total === 0 ? '阅读中标记单词后，会自动加入复习安排。' : '今日已复习按不同单词计数；跳过不计入。'}</span>}<button className="text-button" disabled={blocked} onClick={() => void refresh()}><RefreshCw size={14} />更新</button></div>
      {loadError && <p className="review-inline-warning" role="alert">暂未更新复习安排：{loadError}</p>}
    </section>}
    <section className="panel review-panel" hidden={!active} aria-labelledby="review-title">
      <div className="library-heading review-heading"><div><h2 id="review-title"><RotateCcw size={19} />再见一次，记得更清楚。</h2><p>只复习当前到期的词，每组最多 10 个。记得越稳，下次回顾的间隔越长。</p></div>{session && <span className="count-badge">本组 {session.size} 个词</span>}</div>
      <div className="review-summary">{stats}<div className="today-review-footer">{nextDue ?? <span>时间按当前设备时区显示。</span>}<button className="text-button" disabled={blocked} onClick={() => void refresh()}><RefreshCw size={14} />更新安排</button></div><p>今日已复习按不同单词计数；同一词再次到期仍可练习。</p></div>
      {storageWarning && <p className="review-inline-warning" role="alert">{storageWarning}</p>}
      {restored && item && <p className="review-restored" role="status">已恢复当前浏览器中未完成的一组。{pending ? '有一条自评等待确认，请先重试保存。' : '可以继续上次的进度。'}</p>}
      {loadError && <div className="review-load-error" role="alert"><AlertCircle size={17} /><span>{loadError}</span><button className="text-button" disabled={blocked} onClick={() => void refresh()}><RefreshCw size={14} />重新检查</button></div>}
      {loading && !session && <div className="review-message" role="status"><LoaderCircle size={23} className="spin" /><p>正在准备今日到期的单词…</p></div>}
      {session && <>
        {session.size > 0 && <div className="review-progress"><div><span aria-live="polite">已处理 {processed} / {session.size}<PulseHeart run={confirmedAssessments} active={active} /></span><span>已自评 {session.assessed} · 已跳过 {session.skipped}{session.removed > 0 && ` · 已移出 ${session.removed}`}</span></div><div className="progress-track" role="progressbar" aria-label="本组复习进度" aria-valuemin={0} aria-valuemax={session.size} aria-valuenow={processed}><div style={{ width: `${processed / session.size * 100}%` }} /></div></div>}
        {item && <div className="review-card" aria-busy={saving}>
          <ReviewPractice key={practiceKey} item={item} mode={mode} onModeChange={value => { if (value !== mode) { setRevealedCard(''); setMode(value); } }} active={active} disabled={blocked || Boolean(pending) || !sessionVerified} onReveal={() => setRevealedCard(practiceKey)} onOpenDocument={onOpenDocument} headingRef={wordHeading} meta={<div className="review-meta"><StatusBadge status={latestWord?.status ?? item.vocabulary.status} /><span>{item.last_reviewed_at ? <>最近复习 · <time dateTime={item.last_reviewed_at}>{formatDate(item.last_reviewed_at)}</time></> : '尚未在这里复习'}</span><span>{item.due_at ? <>已到期 · {formatDate(item.due_at)}</> : '首次复习'}</span></div>} />
          {needsReveal && !pending && <p className="review-rating-hint">先检查或揭晓原句答案，再按自己的掌握程度自评。</p>}
          <fieldset className="review-ratings" disabled={blocked || Boolean(pending) || !sessionVerified || needsReveal}><legend>这次，你对它有多熟悉？</legend><div>{ratings.map(rating => <button className={`review-rating ${rating.status.toLowerCase()}`} key={rating.status} onClick={() => void assess(rating.status)}><rating.icon size={20} /><strong>{rating.label}</strong><span>{rating.description}</span><small>下次 · {intervalLabel(item.next_intervals_seconds[rating.status])}后</small></button>)}</div></fieldset>
          {pending && <div className="review-pending" role="status"><Clock3 size={16} /><p>待确认的自评：{ratings.find(rating => rating.status === pending.status)?.label}。请先重试原评价，再继续其他单词；重试不会重复记分。</p></div>}
          {(saveError || pending) && <div className="review-save-error" role={saveError ? 'alert' : undefined}>{saveError && <p><AlertCircle size={16} />{saveError}</p>}{(pending?.status ?? failedStatus) && <button className="text-button" disabled={blocked} onClick={() => void assess((pending?.status ?? failedStatus)!)}><RefreshCw size={14} />重试保存</button>}{canDismiss && <><p>这张卡无法继续确认。可以放下它，已保存的学习记录不会被撤销。</p><button className="text-button" disabled={blocked} onClick={() => skip(true)}>放下这张卡</button></>}</div>}
          <div className="review-card-footer"><p>{saving ? <><LoaderCircle className="spin" size={14} />正在保存自评…</> : '自评后自动安排下次复习，进度保存在当前浏览器。'}</p><button className="text-button" disabled={blocked || Boolean(pending)} onClick={() => skip()}><SkipForward size={15} />跳过这个词</button></div>
        </div>}
        {finished && <div className="review-message review-complete"><span className="review-complete-icon"><Check size={29} /></span><h3 ref={summaryHeading} tabIndex={-1}>这一组，已经走完了。</h3><p>已自评 <strong>{session.assessed}</strong> 个词 · 已跳过 <strong>{session.skipped}</strong> 个词</p>{session.removed > 0 && <p>另有 {session.removed} 个词已移出本组，未计入本次自评。</p>}<p>已自评的词会按新间隔再出现；跳过的词保留原安排。</p><button className="button primary" disabled={blocked} onClick={() => void refresh(true)}><RefreshCw size={16} />加载下一组</button></div>}
        {session.size === 0 && <div className="review-message"><BookOpen size={32} /><h3>{summary?.total ? '有新的单词到期了' : '当前没有到期的单词'}</h3><p>{summary?.total ? '可以开始下一组复习。' : summary?.next_due_at ? <>下一批单词将在 {formatDate(summary.next_due_at)} 到期，可以先继续阅读。</> : '阅读时把单词标为「不认识」或「不确定」，就能在这里再次遇见。'}</p><button className="button secondary" disabled={blocked} onClick={() => void refresh(true)}><RefreshCw size={15} />{summary?.total ? '加载下一组' : '重新检查词库'}</button></div>}
      </>}
      <div className="review-note">不认识：10 分钟后；不确定：1 天后；连续掌握：3 / 7 / 14 / 30 / 60 天后。普通标记为已掌握的词也会在 3 天后回顾。揭晓、填空和跳过不会修改复习安排，只有明确自评才会安排下次复习。</div>
    </section>
  </>;
}
