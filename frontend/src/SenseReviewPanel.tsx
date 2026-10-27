import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, BookOpen, Check, CircleHelp, Clock3, Eye, LoaderCircle, RefreshCw, SkipForward, X } from 'lucide-react';
import { ApiError, errorMessage, formatDate, learningStorageKey, request } from './api';
import { intervalLabel } from './reviewStorage';
import { senseCardKey, senseReviewPath, vocabularySensesPath } from './senseLearning';
import { finishSenseReviewCard, newSenseReviewSession, readSenseReviewSession, reconcileSenseReviewSession, writeSenseReviewSession } from './senseReviewStorage';
import type { SenseReviewSession } from './senseReviewStorage';
import { elapsedCardTimer, frozenResponseMs, memoryStateLabel, newCardTimer, normalizedStudyPlan, pauseCardTimer as pauseCardClock, planReasonLabel, startCardTimer } from './studyPlan';
import StudyPlanOverview from './StudyPlanOverview';
import { StatusBadge } from './WordControls';
import type { SenseReviewBatch, StudyPlanMinutes, WordStatus } from './types';
import './senseReview.css';

const ratings = [
  { status: 'KNOWN', label: '已掌握', description: '能准确想起这个含义', Icon: Check },
  { status: 'UNCERTAIN', label: '不确定', description: '知道大概但不够确定', Icon: CircleHelp },
  { status: 'UNKNOWN', label: '不认识', description: '没有想起这个含义', Icon: X },
] as const;

export default function SenseReviewPanel({ active, disabled, planMinutes, planStorageWarning, onMinutesChange, onLearningChanged }: {
  active: boolean; disabled: boolean; planMinutes: StudyPlanMinutes; planStorageWarning?: string;
  onMinutesChange: (value: StudyPlanMinutes) => void; onLearningChanged?: () => void | Promise<void>;
}) {
  const [session, setSession] = useState<SenseReviewSession | null>(null);
  const [summary, setSummary] = useState<SenseReviewBatch | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [storageWarning, setStorageWarning] = useState('');
  const [restored, setRestored] = useState(false);
  const [verified, setVerified] = useState(false);
  const [revealedKey, setRevealedKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [failedStatus, setFailedStatus] = useState<WordStatus | null>(null);
  const [canDismiss, setCanDismiss] = useState(false);
  const sessionRef = useRef<SenseReviewSession | null>(null);
  const storageKey = useRef('');
  const restoreAttempted = useRef(false);
  const loadingRef = useRef(false);
  const savingRef = useRef(false);
  const activeRef = useRef(active);
  const planMinutesRef = useRef(planMinutes);
  const heading = useRef<HTMLHeadingElement>(null);
  const summaryHeading = useRef<HTMLHeadingElement>(null);
  const cardTimer = useRef<{ key: string } & ReturnType<typeof newCardTimer>>({ key: '', ...newCardTimer() });
  activeRef.current = active;
  planMinutesRef.current = planMinutes;

  const commit = useCallback((value: SenseReviewSession, strict = false, afterWrite = false) => {
    try {
      if (!storageKey.current) throw new Error('浏览器义项复习进度尚未准备好，请重新检查后重试。');
      writeSenseReviewSession(storageKey.current, value);
      if (strict) setStorageWarning('');
    } catch (error) {
      if (strict) throw error;
      setStorageWarning(afterWrite ? '自评已保存，但浏览器进度未能更新。刷新后可重试确认，学习记录不会重复计入。' : errorMessage(error));
    }
    sessionRef.current = value; setSession(value);
  }, []);

  const refresh = useCallback(async (next = false, preserveGroup = false) => {
    if (!activeRef.current || loadingRef.current || savingRef.current) return;
    loadingRef.current = true; setLoading(true); setLoadError('');
    try {
      if (!restoreAttempted.current) {
        storageKey.current = await learningStorageKey('sense-review:v1');
        const saved = readSenseReviewSession(storageKey.current);
        setStorageWarning(saved.warning);
        if (saved.session) {
          sessionRef.current = saved.session; setSession(saved.session);
          setRestored(saved.session.cursor < saved.session.items.length);
        }
        restoreAttempted.current = true;
      }
      const current = sessionRef.current;
      const ids = current && !next && !preserveGroup
        ? current.items.slice(current.cursor).flatMap(item => item.sense.id === null ? [] : [item.sense.id]) : undefined;
      const batch = await request<SenseReviewBatch>(senseReviewPath(ids, planMinutesRef.current));
      setSummary(batch);
      if (!preserveGroup) {
        commit(next || !current ? newSenseReviewSession(batch.items) : reconcileSenseReviewSession(current, batch.items));
        setVerified(true);
        if (next) { setRestored(false); setRevealedKey(''); }
      }
    } catch (error) { setLoadError(errorMessage(error)); }
    finally { loadingRef.current = false; setLoading(false); }
  }, [commit]);

  useEffect(() => { if (active && !disabled) void refresh(); }, [active, disabled, refresh]);
  useEffect(() => {
    if (!active || !restoreAttempted.current) return;
    void refresh(false, true);
  }, [active, planMinutes, refresh]);

  const item = session?.items[session.cursor];
  const itemKey = item ? senseCardKey(item.sense, item.vocabulary.id) : '';
  const pending = session?.pending;
  const revealed = Boolean(itemKey && revealedKey === itemKey);
  const finished = Boolean(session && !item && session.size > 0);
  const plan = normalizedStudyPlan(summary?.plan, summary ?? {
    total: 0, reviewed_today: 0, later_today: 0, next_due_at: null, as_of: new Date(0).toISOString(),
  }, planMinutes);

  const pauseCardTimer = useCallback(() => {
    if (cardTimer.current.startedAt !== null) Object.assign(cardTimer.current, pauseCardClock(cardTimer.current, performance.now()));
  }, []);
  const responseTime = useCallback(() => frozenResponseMs(elapsedCardTimer(cardTimer.current, performance.now())), []);
  useEffect(() => {
    const sync = () => {
      if (cardTimer.current.key !== itemKey) { pauseCardTimer(); cardTimer.current = { key: itemKey, ...newCardTimer() }; }
      const shouldRun = Boolean(itemKey && active && !disabled && !loading && !saving && verified && !pending && window.document.visibilityState === 'visible');
      if (shouldRun) Object.assign(cardTimer.current, startCardTimer(cardTimer.current, performance.now()));
      else pauseCardTimer();
    };
    sync(); window.document.addEventListener('visibilitychange', sync);
    return () => { window.document.removeEventListener('visibilitychange', sync); pauseCardTimer(); };
  }, [active, disabled, itemKey, loading, pauseCardTimer, pending, saving, verified]);
  useEffect(() => { setSaveError(''); setFailedStatus(null); setCanDismiss(false); setRevealedKey(''); }, [itemKey]);
  useEffect(() => {
    if (!active || loading) return;
    if (item) heading.current?.focus(); else if (finished) summaryHeading.current?.focus();
  }, [active, finished, item, itemKey, loading]);

  const assess = async (status: WordStatus) => {
    const current = sessionRef.current;
    const card = current?.items[current.cursor];
    if (!current || !card || savingRef.current || disabled || loadingRef.current || (!verified && !current.pending) || (!revealed && !current.pending)) return;
    if (current.pending && current.pending.status !== status) return;
    savingRef.current = true; setSaving(true); setSaveError(''); setCanDismiss(false); setFailedStatus(status);
    let confirmed = false;
    try {
      pauseCardTimer();
      const attempt = current.pending ? {
        ...current.pending,
        responseMs: current.pending.responseMs ?? responseTime(),
        planMinutes: current.pending.planMinutes ?? planMinutesRef.current,
      } : { item: card, status, requestId: crypto.randomUUID(), responseMs: responseTime(), planMinutes: planMinutesRef.current };
      commit({ ...current, pending: attempt }, true);
      await request(vocabularySensesPath(attempt.item.vocabulary.word, attempt.item.sense.sense_key), {
        method: 'PUT', body: JSON.stringify({ status: attempt.status, origin: 'review', request_id: attempt.requestId,
          response_ms: attempt.responseMs, plan_minutes: attempt.planMinutes }),
      });
      confirmed = true;
      commit(finishSenseReviewCard(sessionRef.current!, 'assessed'), false, true);
      setFailedStatus(null);
      void onLearningChanged?.();
    } catch (error) {
      setSaveError(`${errorMessage(error)} 当前义项仍保留，请重试确认本次自评。`);
      setCanDismiss(error instanceof ApiError && (error.status === 404 || error.status === 409));
    } finally {
      savingRef.current = false; setSaving(false);
      if (confirmed) void refresh();
    }
  };
  const skip = (dismiss = false) => {
    const current = sessionRef.current;
    if (!current || savingRef.current || disabled || loadingRef.current || (current.pending && !dismiss)) return;
    pauseCardTimer(); commit(finishSenseReviewCard(current, dismiss ? 'removed' : 'skipped'));
  };

  const blocked = disabled || saving || loading;
  const processed = session ? session.assessed + session.skipped + session.removed : 0;
  return <div hidden={!active} className="sense-review-mode">
    {summary && <StudyPlanOverview subject="sense" plan={plan} targetMinutes={planMinutes} disabled={disabled || loading} storageWarning={planStorageWarning} onMinutesChange={onMinutesChange} />}
    <section className="panel review-panel sense-review-panel" aria-labelledby="sense-review-title">
      <div className="library-heading review-heading"><div><h2 id="sense-review-title"><BookOpen size={19} />一个含义，一次判断。</h2><p>同一个词的不同含义会分开出现；先回想，再揭晓中文释义并评价。</p></div>{session && <span className="count-badge">本组 {session.size} 个义项</span>}</div>
      {summary && <div className="review-summary"><div className="review-stats" aria-label="今日义项复习统计"><div><span>当前到期</span><strong>{summary.total}</strong></div><div><span>今日已复习</span><strong>{summary.reviewed_today}</strong></div><div><span>今日稍后到期</span><strong>{summary.later_today}</strong></div></div><div className="today-review-footer"><span>{summary.next_due_at ? <>下次到期 · <time dateTime={summary.next_due_at}>{formatDate(summary.next_due_at)}</time></> : '时间按当前设备时区显示。'}</span><button className="text-button" disabled={blocked} onClick={() => void refresh()}><RefreshCw size={14} />更新安排</button></div></div>}
      {storageWarning && <p className="review-inline-warning" role="alert">{storageWarning}</p>}
      {restored && item && <p className="review-restored" role="status">已恢复未完成的义项复习。{pending ? '有一条自评等待确认，请先重试保存。' : '可以继续上次的进度。'}</p>}
      {loadError && <div className="review-load-error" role="alert"><AlertCircle size={17} /><span>{loadError}</span><button className="text-button" disabled={blocked} onClick={() => void refresh()}><RefreshCw size={14} />重新检查</button></div>}
      {loading && !session && <div className="review-message" role="status"><LoaderCircle size={23} className="spin" /><p>正在准备今日到期的义项…</p></div>}
      {session && <>
        {session.size > 0 && <div className="review-progress"><div><span aria-live="polite">已处理 {processed} / {session.size}</span><span>已自评 {session.assessed} · 已跳过 {session.skipped}{session.removed > 0 && ` · 已移出 ${session.removed}`}</span></div><div className="progress-track" role="progressbar" aria-label="本组义项复习进度" aria-valuemin={0} aria-valuemax={session.size} aria-valuenow={processed}><div style={{ width: `${processed / session.size * 100}%` }} /></div></div>}
        {item && <article className="review-card sense-review-card" aria-busy={saving}>
          <div className="sense-review-prompt"><span className="overline">SENSE RECALL · 第 {item.sense.source_ordinal + 1} 义项</span><h3 ref={heading} tabIndex={-1} lang="en">{item.vocabulary.word}</h3><div className="sense-review-meta"><StatusBadge status={item.sense.status} />{item.sense.part_of_speech && <span>{item.sense.part_of_speech}</span>}<span>安排原因 · {planReasonLabel(item.plan_reason)}</span><span>记忆状态 · {memoryStateLabel(item.memory_state)}</span></div><p>这个词在当前义项下表示什么？</p></div>
          {!item.sense.available && <p className="sense-review-snapshot">词典来源已更新，本卡使用你已学过的义项快照。</p>}
          {!revealed && !pending ? <button type="button" className="button primary sense-review-reveal" disabled={blocked || !verified} onClick={() => setRevealedKey(itemKey)}><Eye size={17} />揭晓这个义项</button> : <div className="sense-review-answer" role="status" aria-label="义项答案"><span>中文释义</span><p>{item.sense.meaning}</p></div>}
          {!revealed && !pending && <p className="review-rating-hint">揭晓前不会显示中文答案，也不能提交评价。</p>}
          <fieldset className="review-ratings" disabled={blocked || Boolean(pending) || !verified || !revealed}><legend>你记住的是这个具体含义吗？</legend><div>{ratings.map(({ status, label, description, Icon }) => <button type="button" className={`review-rating ${status.toLowerCase()}`} key={status} onClick={() => void assess(status)}><Icon size={20} /><strong>{label}</strong><span>{description}</span><small>下次 · {intervalLabel(item.next_intervals_seconds[status])}后</small></button>)}</div></fieldset>
          {pending && <div className="review-pending" role="status"><Clock3 size={16} /><p>待确认：{ratings.find(rating => rating.status === pending.status)?.label}。重试会确认同一个义项，不会切到这个词的其他含义。</p></div>}
          {(saveError || pending) && <div className="review-save-error" role={saveError ? 'alert' : undefined}>{saveError && <p><AlertCircle size={16} />{saveError}</p>}{(pending?.status ?? failedStatus) && <button className="text-button" disabled={blocked} onClick={() => void assess((pending?.status ?? failedStatus)!)}><RefreshCw size={14} />重试保存</button>}{canDismiss && <button className="text-button" disabled={blocked} onClick={() => skip(true)}>放下这张义项卡</button>}</div>}
          <div className="review-card-footer"><p>{saving ? <><LoaderCircle className="spin" size={14} />正在保存义项自评…</> : '本次评价只更新当前义项，不会覆盖整词或其他含义。'}</p><button className="text-button" disabled={blocked || Boolean(pending)} onClick={() => skip()}><SkipForward size={15} />跳过这个义项</button></div>
        </article>}
        {finished && <div className="review-message review-complete"><span className="review-complete-icon"><Check size={29} /></span><h3 ref={summaryHeading} tabIndex={-1}>这一组义项，已经走完了。</h3><p>已自评 <strong>{session.assessed}</strong> 项 · 已跳过 <strong>{session.skipped}</strong> 项</p>{plan.available_now > 0 ? <button className="button primary" disabled={blocked} onClick={() => void refresh(true)}><RefreshCw size={16} />加载下一组</button> : <p>{plan.later_today > 0 ? '今天稍后还有义项到期。' : '今天暂时没有更多到期义项。'}</p>}</div>}
        {session.size === 0 && <div className="review-message"><BookOpen size={32} /><h3>{plan.available_now > 0 ? '有一组到期义项可复习' : plan.status === 'COMPLETE' ? '今天的义项计划已完成' : '当前没有到期义项'}</h3><p>{plan.available_now > 0 ? '可以加载下一组。' : '在查词时标记具体释义，它会按自己的节奏来到这里。'}</p>{plan.available_now > 0 && <button className="button secondary" disabled={blocked} onClick={() => void refresh(true)}><RefreshCw size={15} />加载下一组</button>}</div>}
      </>}
      <div className="review-note">“整词复习”回答的是你是否认识这个词；“释义项复习”只回答当前展示的一个含义。两套进度独立保存。</div>
    </section>
  </div>;
}
