import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, BookOpen, CalendarDays, ChevronLeft, ChevronRight, Clock3, LoaderCircle, RefreshCw, Search, X } from 'lucide-react';
import { errorMessage, request } from './api';
import { useDictionary } from './DictionaryPanel';
import { StatusBadge } from './WordControls';
import { groupHistoryEntries, historyActivityLevel, historyCalendarWeeks, historyDateLabel, historyLatestLabel, historyPageNumbers, learningHistoryPath } from './learningHistory';
import type { HistoryContext, HistoryDay, HistoryEntry, HistoryFilters, HistoryKind, HistoryRange, LearningHistory } from './learningHistory';
import './learningHistory.css';

const kinds: { value: HistoryKind; label: string }[] = [{ value: 'all', label: '全部操作' }, { value: 'review', label: '复习自评' },
  { value: 'lookup', label: '主动查词' }, { value: 'mark', label: '状态标记' }, { value: 'related', label: '关联探索' }];
const weekdays = ['一', '二', '三', '四', '五', '六', '日'];

export default function LearningHistoryView({ disabled, onOpenDocument, onReview, revision }: {
  disabled: boolean; onOpenDocument: (documentId: number, occurrenceId: number) => void; onReview: () => void; revision: number;
}) {
  const openDictionary = useDictionary();
  const [days, setDays] = useState<HistoryRange>(30);
  const [filters, setFilters] = useState<HistoryFilters>({ day: null, kind: 'all', q: '', page: 1 });
  const [search, setSearch] = useState('');
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ value: LearningHistory | null; path: string; loading: boolean; error: string }>({ value: null, path: '', loading: true, error: '' });
  const listHeading = useRef<HTMLHeadingElement>(null);
  const movingPage = useRef(false);
  const path = learningHistoryPath(days, filters);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const q = search.trim().slice(0, 100);
      setFilters(current => current.q === q ? current : { ...current, q, page: 1 });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (disabled) { setResult({ value: null, path: '', loading: false, error: '' }); return; }
    const controller = new AbortController();
    setResult(current => ({ ...current, loading: true, error: '' }));
    // This view reads an aggregate only. Looking at a record never counts as studying it.
    request<LearningHistory>(path, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setResult({ value, path, loading: false, error: '' }); })
      .catch(error => { if (!controller.signal.aborted) setResult(current => ({ ...current, loading: false, error: errorMessage(error) })); });
    return () => controller.abort();
  }, [path, disabled, revision, retry]);

  const history = result.value?.range.days === days ? result.value : null;
  const entriesReady = Boolean(history && result.path === path && !result.loading && !result.error && !disabled);
  const groups = useMemo(() => groupHistoryEntries(entriesReady ? history!.entries : []), [history, entriesReady]);
  const summary = history?.summary;
  const selectedDay = history?.days.find(day => day.date === filters.day);
  const page = history?.pagination.page ?? 1;
  const pageCount = history?.pagination.total_pages ?? 1;

  useEffect(() => {
    if (entriesReady && movingPage.current) {
      movingPage.current = false;
      listHeading.current?.focus({ preventScroll: true });
      listHeading.current?.scrollIntoView({ block: 'start', behavior: 'instant' });
    }
  }, [entriesReady]);

  const changeRange = (value: HistoryRange) => {
    if (days === value) return;
    movingPage.current = false;
    setDays(value); setFilters(current => ({ ...current, day: null, page: 1 }));
  };
  const changeDay = (day: string | null) => {
    movingPage.current = false;
    setFilters(current => ({ ...current, day: current.day === day ? null : day, page: 1 }));
  };
  const changePage = (next: number) => {
    if (next < 1 || next > pageCount || next === page) return;
    movingPage.current = true;
    setFilters(current => ({ ...current, page: next }));
  };
  const contextAction = (context: HistoryContext) => onOpenDocument(context.document_id, context.occurrence_id);

  return <div className="learning-history" aria-label="学习记录工作台">
    <div className="lh-toolbar"><p>按日期回顾学习，重读还没记牢的词。</p><div className="lh-toolbar-actions"><div className="lh-ranges" role="group" aria-label="学习记录时间范围">{([7, 30, 90] as const).map(value => <button type="button" key={value} disabled={disabled} aria-pressed={days === value} onClick={() => changeRange(value)}>近 {value} 天</button>)}</div><button type="button" className="lh-refresh" aria-label="刷新学习记录" title="刷新学习记录" disabled={disabled || result.loading} onClick={() => setRetry(value => value + 1)}><RefreshCw size={16} className={result.loading ? 'spin' : ''} /></button></div></div>

    {disabled && <p className="lh-message" role="status">正在处理学习数据，请稍后查看记录。</p>}
    {result.error && <div className="lh-error" role="alert"><p>学习记录暂时无法读取：{result.error}</p><button type="button" className="text-button" disabled={disabled} onClick={() => setRetry(value => value + 1)}>重试学习记录</button></div>}
    {!history && result.loading && <p className="lh-message" role="status"><LoaderCircle size={17} className="spin" />正在整理学习记录…</p>}

    {history && summary && <>
      <section className="lh-summary" aria-label="本期学习统计"><div className="lh-period"><span>本期</span><strong>{history.range.start_date.replaceAll('-', '.')} — {history.range.end_date.slice(5).replace('-', '.')}</strong><small>以下统计覆盖全部 {days} 天</small></div><dl>{[
        { label: '学习过的词', value: summary.studied_words, unit: '个', note: '本期去重' },
        { label: '复习过的词', value: summary.reviewed_words, unit: '个', note: '完成过复习自评' },
        { label: '主动查词', value: summary.lookup_count, unit: '次', note: '主动查看语境释义' },
        { label: '有记录的天数', value: summary.active_days, unit: '天', note: `共 ${days} 天` },
      ].map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value.toLocaleString()}<span>{item.unit}</span></dd><small>{item.note}</small></div>)}</dl></section>

      <div className="lh-overview">
        <section className="lh-calendar-section" aria-labelledby="lh-calendar-heading"><div className="lh-section-heading"><div><h2 id="lh-calendar-heading"><CalendarDays size={17} />每日记录</h2><p>点击日期，回看当天学习过的词。</p></div><div className="lh-legend" aria-label="颜色越深表示学习操作越多"><span>少</span>{[0, 1, 2, 3, 4].map(level => <i key={level} data-level={level} />)}<span>多</span></div></div>
          <HistoryCalendar days={history.days} range={days} selected={filters.day} disabled={disabled} onSelect={changeDay} />
          <div className="lh-calendar-caption"><span>{selectedDay ? <><strong>{historyDateLabel(selectedDay.date)}</strong> · {selectedDay.words} 个词 · {selectedDay.actions} 次操作</> : <>本期共 {summary.total_actions} 次学习操作</>}</span>{filters.day ? <button type="button" className="text-button" disabled={disabled} onClick={() => changeDay(null)}>查看本期全部日期<X size={12} /></button> : <span>颜色按操作次数呈现</span>}</div>
          <p className="lh-scope-note">按本机日期统计。自评是你的主观反馈，不代表测试成绩；导入与撤销记录不计为学习。</p>
        </section>

        <section className="lh-next" aria-labelledby="lh-next-heading"><div><p className="lh-eyebrow">下一步</p><h2 id="lh-next-heading">到期复习</h2><p>按当前安排复习，继续已有进度。</p></div><div className="lh-due"><span>当前到期词</span><strong>{summary.due_total.toLocaleString()}<small> 个</small></strong></div><button type="button" className="button primary" disabled={disabled} onClick={onReview}>去复习到期词<ArrowRight size={16} /></button><small>按当前复习安排计算，独立于本期筛选。</small></section>
      </div>

      <section className="lh-focus" aria-labelledby="lh-focus-heading"><div className="lh-section-heading"><div><h2 id="lh-focus-heading">值得再看的词</h2><p>当前仍不熟悉，且本期反复标记不熟悉或主动查词的词。</p></div><span className="lh-subtle">最多 6 个</span></div>{history.focus_words.length ? <ul className="lh-focus-list">{history.focus_words.map(item => <li className="lh-focus-word" key={item.lemma}><div className="lh-focus-top"><button type="button" className="lh-word-button" disabled={disabled} aria-label={`查看 ${item.word} 的中文释义`} onClick={event => openDictionary(item.lemma, event.currentTarget)} lang="en">{item.word}</button><StatusBadge status={item.status} /></div><p className="lh-meaning">{item.meaning_summary || '点单词查看释义'}</p><p className="lh-evidence">{[item.unfamiliar_count > 0 ? `标记不熟悉 ${item.unfamiliar_count} 次` : '', item.lookup_count > 0 ? `主动查词 ${item.lookup_count} 次` : ''].filter(Boolean).join(' · ')}</p>{item.review_unfamiliar_count > 0 && <p className="lh-evidence">其中复习自评不熟悉 {item.review_unfamiliar_count} 次</p>}{item.context && <button type="button" className="text-button lh-source" disabled={disabled} title={item.context.document_title} onClick={() => contextAction(item.context!)}><BookOpen size={13} />回原文</button>}</li>)}</ul> : <div className="lh-focus-empty"><BookOpen size={18} /><p>这段时间没有需要优先回看的词。出现至少 2 次不熟悉标记，或 2 次主动查词后，会在这里整理出来。</p></div>}</section>
    </>}

    {history && <section className="lh-records" aria-labelledby="lh-records-heading"><div className="lh-records-heading"><div><h2 id="lh-records-heading" ref={listHeading} tabIndex={-1}>{filters.day ? `${historyDateLabel(filters.day)}的学习记录` : '本期学习明细'}</h2><p>同一天的同一个词合并显示，保留各类操作次数。</p></div>{entriesReady && <span className="lh-subtle">{history.pagination.total} 条词汇日记录</span>}</div>
      <div className="lh-filters"><label className="lh-search"><Search size={16} /><input type="search" aria-label="搜索英文单词" placeholder="搜索英文单词" maxLength={100} value={search} disabled={disabled} onChange={event => setSearch(event.target.value)} /></label><label className="lh-kind"><span>操作</span><select aria-label="筛选学习操作" value={filters.kind} disabled={disabled} onChange={event => { movingPage.current = false; setFilters(current => ({ ...current, kind: event.target.value as HistoryKind, page: 1 })); }}>{kinds.map(kind => <option key={kind.value} value={kind.value}>{kind.label}</option>)}</select></label>{(filters.day || filters.kind !== 'all' || search) && <button type="button" className="text-button lh-clear" disabled={disabled} onClick={() => { movingPage.current = false; setSearch(''); setFilters({ day: null, kind: 'all', q: '', page: 1 }); }}>清除筛选<X size={13} /></button>}</div>
      <div className="lh-entry-area" aria-busy={result.loading || (!entriesReady && !result.error && !disabled)}>
        {(result.loading || (!entriesReady && !result.error && !disabled)) && <p className="lh-message" role="status"><LoaderCircle size={16} className="spin" />正在读取学习明细…</p>}
        {entriesReady && groups.length === 0 && <div className="lh-empty"><Clock3 size={26} /><h3>{filters.day || filters.kind !== 'all' || filters.q ? '没有符合筛选条件的记录' : '这段时间还没有学习记录'}</h3><p>{filters.day || filters.kind !== 'all' || filters.q ? '换一个日期、操作类型或单词试试。' : '在阅读中主动查词、标记，或完成复习自评后，就能在这里回看。'}</p></div>}
        {entriesReady && groups.map(group => <section className="lh-day-group" key={group.date} aria-label={`${group.date}的学习记录`}><div className="lh-day-heading"><h3><time dateTime={group.date}>{historyDateLabel(group.date, true)}</time></h3><span>{group.date}</span></div><ul>{group.entries.map(entry => <HistoryRecord key={entry.key} entry={entry} disabled={disabled} onDictionary={openDictionary} onContext={contextAction} />)}</ul></section>)}
      </div>
      {entriesReady && history.pagination.total > 0 && <footer className="lh-pagination"><span>第 {page} / {pageCount} 页 · 每页最多 {history.pagination.page_size} 条</span><nav aria-label="学习记录分页"><button type="button" aria-label="上一页" disabled={disabled || page <= 1} onClick={() => changePage(page - 1)}><ChevronLeft size={16} /></button>{historyPageNumbers(page, pageCount).map(item => typeof item === 'number' ? <button type="button" key={item} aria-label={`第 ${item} 页`} aria-current={item === page ? 'page' : undefined} disabled={disabled} onClick={() => changePage(item)}>{item}</button> : <span key={item} aria-hidden="true">…</span>)}<button type="button" aria-label="下一页" disabled={disabled || page >= pageCount} onClick={() => changePage(page + 1)}><ChevronRight size={16} /></button></nav></footer>}
      <p className="lh-record-note">单词旁的状态是当前自评。点击单词查看释义，或回原文重读；浏览本页不会增加查词次数或改变复习安排。</p>
    </section>}
  </div>;
}

function HistoryCalendar({ days, range, selected, disabled, onSelect }: {
  days: HistoryDay[]; range: HistoryRange; selected: string | null; disabled: boolean; onSelect: (date: string) => void;
}) {
  const weeks = historyCalendarWeeks(days);
  const dayButton = (day: HistoryDay, short: boolean) => <button type="button" key={day.date} data-date={day.date} data-level={historyActivityLevel(day.actions)} disabled={disabled} aria-pressed={selected === day.date}
    aria-label={`${day.date}，${day.words} 个词，${day.actions} 次学习操作`} title={`${historyDateLabel(day.date, true)} · ${day.words} 个词 · ${day.actions} 次操作`} onClick={() => onSelect(day.date)}>{short && <small>{new Intl.DateTimeFormat('zh-CN', { timeZone: 'UTC', weekday: 'short' }).format(new Date(`${day.date}T12:00:00Z`))}</small>}<span>{Number(day.date.slice(8))}</span>{short && <small>{day.words} 个词</small>}</button>;
  if (range === 7) return <div className="lh-week-strip" aria-label="每日学习活动">{days.map(day => dayButton(day, true))}</div>;
  return <div className="lh-calendar-scroll"><div className="lh-heatmap" aria-label="每日学习活动"><div className="lh-weekdays" aria-hidden="true"><span />{weekdays.map(day => <span key={day}>{day}</span>)}</div>{weeks.map((week, index) => {
    const first = week.find(day => day !== null);
    const month = week.find(day => day?.date.endsWith('-01'));
    return <div className="lh-calendar-week" key={first?.date ?? index}><span className="lh-month">{month ? `${Number(month.date.slice(5, 7))}月` : index === 0 && first ? `${Number(first.date.slice(5, 7))}月` : ''}</span>{week.map((day, dayIndex) => day ? dayButton(day, false) : <span className="lh-calendar-gap" key={`gap-${dayIndex}`} />)}</div>;
  })}</div></div>;
}

function HistoryRecord({ entry, disabled, onDictionary, onContext }: {
  entry: HistoryEntry; disabled: boolean; onDictionary: (word: string, opener: HTMLElement) => void; onContext: (context: HistoryContext) => void;
}) {
  const counts = [{ count: entry.review_count, label: '复习自评' }, { count: entry.lookup_count, label: '主动查词' }, { count: entry.mark_count, label: '状态标记' }, { count: entry.related_count, label: '关联探索' }].filter(item => item.count > 0);
  return <li className="learning-history-entry" data-key={entry.key}><div className="lh-entry-main"><div className="lh-entry-word"><button type="button" className="lh-word-button" disabled={disabled} aria-label={`查看 ${entry.word} 的中文释义`} onClick={event => onDictionary(entry.lemma, event.currentTarget)} lang="en">{entry.word}</button><StatusBadge status={entry.status} /></div><p className="lh-meaning">{entry.meaning_summary || '点单词查看释义'}</p><div className="lh-entry-counts">{counts.map(item => <span key={item.label}>{item.label}<strong>{item.count}</strong>次</span>)}</div></div><div className="lh-entry-detail"><p className="lh-latest"><time dateTime={entry.last_at}>{new Date(entry.last_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })}</time><span>{historyLatestLabel(entry.last_event_type, entry.last_was_review)}</span></p>{entry.context ? <div className="lh-entry-context"><button type="button" className="text-button" disabled={disabled} onClick={() => onContext(entry.context!)} title={entry.context.document_title}><BookOpen size={13} />回原文<ArrowRight size={12} /></button><span title={entry.context.document_title}>{entry.context.document_title}</span>{entry.context.sentence && <details><summary>查看原句</summary><p lang="en">{entry.context.sentence}</p></details>}</div> : <span className="lh-no-context">未关联可用原文</span>}</div></li>;
}
