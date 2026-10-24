import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, BookOpen, Check, ChevronLeft, ChevronRight, Layers3, LoaderCircle, Plus, RefreshCw, Search, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import { errorMessage, request } from './api';
import { useDictionary } from './DictionaryPanel';
import { StatusBadge } from './WordControls';
import WordExpansion from './WordExpansionPanel';
import type { RecommendationFeedback, StoredRelationType, Vocabulary, WordStatus } from './types';
import type { WordExpansionCache } from './wordExpansion';
import { findRelationSource, relationPageNumbers, relationPracticeItems, relationRecallMeaning, relationSourceSuggestions, relationWorkspaceLabels, relationWorkspacePath } from './relationWorkspace';
import type { RelationContext, RelationWorkspace, RelationWorkspaceFilters, WorkspaceRelation } from './relationWorkspace';
import './relationWorkspace.css';

type FeedbackAction = 'UNKNOWN' | 'USEFUL' | 'NOT_USEFUL';
type Attempt = { action: FeedbackAction; request_id: string; batch_id: number; word: string };
type RowFeedback = { status?: WordStatus | null; useful?: boolean | null; failed?: Attempt & { message: string } };
type Practice = { items: WorkspaceRelation[]; index: number; revealed: boolean; missed: WorkspaceRelation[] };
const defaultFilters: RelationWorkspaceFilters = { q: '', relation: 'all', scope: 'all', include_dismissed: false, page: 1 };

export default function RelationsView({ vocabulary, disabled, revision, onLearningChanged, onOpenDocument, onReview, onBusyChange }: {
  vocabulary: Vocabulary[]; disabled: boolean; revision: number; onLearningChanged: () => Promise<void>;
  onOpenDocument: (documentId: number, occurrenceId: number) => void; onReview: () => void; onBusyChange: (busy: boolean) => void;
}) {
  const openDictionary = useDictionary();
  const cache = useRef<WordExpansionCache>(new Map());
  const [sourceQuery, setSourceQuery] = useState('');
  const [selectedWord, setSelectedWord] = useState('');
  const [filters, setFilters] = useState<RelationWorkspaceFilters>(defaultFilters);
  const [search, setSearch] = useState('');
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ data: RelationWorkspace | null; path: string; loading: boolean; error: string }>({ data: null, path: '', loading: true, error: '' });
  const [feedback, setFeedback] = useState<Record<number, RowFeedback>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [syncError, setSyncError] = useState('');
  const [practice, setPractice] = useState<Practice | null>(null);
  const feedbackBusy = useRef(false);
  const active = useRef(true);
  const listHeading = useRef<HTMLHeadingElement>(null);
  const practiceHeading = useRef<HTMLHeadingElement>(null);
  const movingPage = useRef(false);
  const path = relationWorkspacePath(filters);
  // A parent-wide mutation lock is also set by this view's own feedback. Keep its pending row mounted.
  const externallyDisabled = disabled && busyId === null;
  const blocked = disabled || busyId !== null;
  const selected = findRelationSource(vocabulary, selectedWord);
  const suggestions = useMemo(() => relationSourceSuggestions(vocabulary, sourceQuery), [vocabulary, sourceQuery]);

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const q = search.trim().slice(0, 100);
      setFilters(current => current.q === q ? current : { ...current, q, page: 1 });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (externallyDisabled) { setResult({ data: null, path: '', loading: false, error: '' }); return; }
    const controller = new AbortController();
    setResult(current => ({ ...current, loading: true, error: '' }));
    request<RelationWorkspace>(path, { signal: controller.signal })
      .then(data => {
        if (controller.signal.aborted) return;
        setResult({ data, path, loading: false, error: '' });
        // Fresh server state supersedes optimistic acknowledgements. Only an unconfirmed
        // attempt retains its original id so a transport retry cannot create another action.
        setFeedback(current => Object.fromEntries(Object.entries(current).filter(([, value]) => value.failed)
          .map(([key, value]) => [key, { failed: value.failed }])));
      })
      .catch(error => { if (!controller.signal.aborted) setResult(current => ({ ...current, loading: false, error: errorMessage(error) })); });
    return () => controller.abort();
  }, [path, externallyDisabled, revision, retry]);

  useEffect(() => {
    if (externallyDisabled) setPractice(null);
  }, [externallyDisabled]);

  const ready = Boolean(result.data && result.path === path && !result.loading && !result.error && !externallyDisabled);
  const listVisible = Boolean(result.data && result.path === path && !externallyDisabled);
  const feedbackBlocked = blocked || !ready;
  const items = useMemo(() => listVisible ? result.data!.items.map(item => {
    const local = feedback[item.id];
    return { ...item, target: { ...item.target, status: local?.status ?? item.target.status },
      useful: local?.useful === undefined ? item.useful : local.useful };
  }) : [], [listVisible, result.data, feedback]);
  const practiceItems = useMemo(() => relationPracticeItems(items), [items]);
  const page = result.data?.pagination.page ?? 1;
  const totalPages = result.data?.pagination.total_pages ?? 1;

  useEffect(() => {
    if (ready && movingPage.current) {
      movingPage.current = false;
      listHeading.current?.focus({ preventScroll: true });
      listHeading.current?.scrollIntoView({ block: 'start', behavior: 'instant' });
    }
  }, [ready]);

  useEffect(() => {
    if (practice) practiceHeading.current?.focus({ preventScroll: true });
  }, [practice?.index, practice?.items]);

  const refreshLearning = async () => {
    try { await onLearningChanged(); if (active.current) setSyncError(''); }
    catch (error) { if (active.current) setSyncError(errorMessage(error)); }
  };

  const sendFeedback = async (item: WorkspaceRelation, attempt: Attempt) => {
    if (!attempt.batch_id || feedbackBusy.current || disabled || !ready) return;
    if (attempt.action === 'UNKNOWN' && findRelationSource(vocabulary, attempt.word)?.status === 'KNOWN') return;
    feedbackBusy.current = true;
    setBusyId(item.id); onBusyChange(true);
    try {
      const value = await request<RecommendationFeedback>(`/recommendations/${attempt.batch_id}/feedback`, {
        method: 'POST', body: JSON.stringify({ word: attempt.word, action: attempt.action, request_id: attempt.request_id }),
      });
      if (active.current) {
        setFeedback(current => ({ ...current, [item.id]: { status: value.vocabulary?.status ?? value.status ?? item.target.status, useful: value.useful } }));
        setRetry(value => value + 1);
      }
      // A saved action is not reported as failed merely because refreshing another panel failed.
      await refreshLearning();
    } catch (error) {
      if (active.current) setFeedback(current => ({ ...current, [item.id]: { ...current[item.id], failed: { ...attempt, message: errorMessage(error) } } }));
    } finally {
      feedbackBusy.current = false;
      if (active.current) setBusyId(null);
      onBusyChange(false);
    }
  };

  const chooseFeedback = (item: WorkspaceRelation, action: FeedbackAction) => {
    if (feedbackBlocked || feedback[item.id]?.failed || !item.batch_id) return;
    const owned = findRelationSource(vocabulary, item.target_word);
    if (action === 'UNKNOWN' && (owned?.status === 'KNOWN' || owned?.status === 'UNKNOWN')) return;
    void sendFeedback(item, { action, request_id: crypto.randomUUID(), batch_id: item.batch_id, word: item.target_word });
  };
  const openContext = (context: RelationContext) => onOpenDocument(context.document_id, context.occurrence_id);
  const pickSource = (word: string) => {
    if (blocked || !findRelationSource(vocabulary, word)) return;
    setSelectedWord(word); setSourceQuery('');
  };
  const changePage = (value: number) => {
    if (blocked || value < 1 || value > totalPages || value === page) return;
    movingPage.current = true; setFilters(current => ({ ...current, page: value }));
  };
  const startPractice = (values: WorkspaceRelation[]) => {
    if (blocked || !values.length) return;
    setPractice({ items: [...values], index: 0, revealed: false, missed: [] });
    window.requestAnimationFrame(() => practiceHeading.current?.scrollIntoView({ block: 'start', behavior: 'instant' }));
  };
  const answerPractice = (remembered: boolean) => {
    if (blocked) return;
    setPractice(current => !current || !current.revealed ? current : { ...current, index: current.index + 1, revealed: false,
      missed: remembered ? current.missed : [...current.missed, current.items[current.index]] });
  };

  const wordButton = (word: string) => <button type="button" className="rw-word" disabled={blocked}
    aria-label={`查看 ${word} 的中文释义`} lang="en" onClick={event => openDictionary(word, event.currentTarget)}>{word}</button>;

  const rowActions = (item: WorkspaceRelation) => {
    const local = feedback[item.id];
    const latest = result.data?.items.find(value => value.id === item.id);
    const owned = findRelationSource(vocabulary, item.target_word);
    // Practice keeps the original explanatory snapshot, but actions use current learning state.
    const status = owned?.status === 'KNOWN' ? 'KNOWN' : local?.status ?? latest?.target.status ?? owned?.status ?? item.target.status;
    const useful = local?.useful === undefined ? latest?.useful ?? item.useful : local.useful;
    return <div className="rw-actions-area">
      <div className="rw-row-actions">
        {item.batch_id ? <>
          <button type="button" className="rw-add" disabled={feedbackBlocked || Boolean(local?.failed) || status === 'KNOWN' || status === 'UNKNOWN'}
            aria-label={`加入 ${item.target_word} 到生词库`} onClick={() => chooseFeedback(item, 'UNKNOWN')}>
            {status === 'KNOWN' || status === 'UNKNOWN' ? <Check size={13} /> : <Plus size={13} />}{status === 'KNOWN' ? '已在词库 · 已掌握' : status === 'UNKNOWN' ? '已在生词库' : '加入生词'}</button>
          <div className="rw-utility" role="group" aria-label={`${item.source_word} 与 ${item.target_word} 的帮助程度`}>
            <button type="button" aria-label={useful === false ? '恢复这个关联' : '这个关联有用'} title={useful === false ? '恢复并标为有用' : '这个关联有用'} aria-pressed={useful === true} disabled={feedbackBlocked || Boolean(local?.failed) || useful === true} onClick={() => chooseFeedback(item, 'USEFUL')}><ThumbsUp size={13} /><span>{useful === false ? '恢复关联' : useful === true ? '已标为有用' : '有用'}</span></button>
            <button type="button" aria-label="略过这个关联" title="不再显示这组关联" aria-pressed={useful === false} disabled={feedbackBlocked || Boolean(local?.failed) || useful === false} onClick={() => chooseFeedback(item, 'NOT_USEFUL')}><ThumbsDown size={13} /><span>{useful === false ? '已略过' : '略过'}</span></button>
          </div>
        </> : <span className="rw-missing">原推荐暂不可操作，仍可查词与回想。</span>}
        {item.context && <button type="button" className="text-button rw-context-button" aria-label={`回原文查看 ${item.source_word}`} title={item.context.document_title} disabled={blocked} onClick={() => openContext(item.context!)}><BookOpen size={13} />回原文</button>}
      </div>
      {busyId === item.id && <p className="rw-save-status" role="status"><LoaderCircle size={12} className="spin" />正在保存…</p>}
      {local?.failed && <div className="rw-row-error" role="alert">{local.failed.action === 'UNKNOWN' && status === 'KNOWN' ? <><p>这个词当前已掌握，无需重试加入生词。</p><button type="button" className="text-button" disabled={feedbackBlocked} onClick={() => setFeedback(current => ({ ...current, [item.id]: { ...current[item.id], failed: undefined } }))}>关闭这次失败提示</button></> : <><p>反馈未确认保存：{local.failed.message}</p><button type="button" className="text-button" disabled={feedbackBlocked} onClick={() => void sendFeedback(item, local.failed!)}><RefreshCw size={13} />重试保存反馈</button></>}</div>}
    </div>;
  };

  const activePractice = practice && practice.index < practice.items.length;
  const practiceItem = activePractice ? practice.items[practice.index] : null;

  return <div className="relation-workspace" aria-label="词汇关联工作台">
    <div className="rw-toolbar"><p>比较用法，记住联系，再试着回想。</p><button type="button" className="text-button" disabled={blocked || result.loading} onClick={() => setRetry(value => value + 1)}><RefreshCw size={14} className={result.loading ? 'spin' : ''} />刷新关联</button></div>
    {externallyDisabled && <p className="rw-message" role="status">正在处理学习数据，关联操作暂时暂停。</p>}
    {!externallyDisabled && result.data && <section className="rw-summary" aria-label="词汇关联概况"><dl>{[
      ['保存的关联', result.data.summary.total_relations, '组'], ['关联起点', result.data.summary.source_words, '个词'],
      ['尚未掌握的关联词', result.data.summary.unfamiliar_targets, '个'], ['有释义的关联', result.data.summary.practice_pairs, '组'],
    ].map(([label, value, unit]) => <div key={label}><dt>{label}</dt><dd>{Number(value).toLocaleString()}<span>{unit}</span></dd></div>)}</dl><p>统计全部未略过的关联，不随下方筛选变化。当前词库状态会同步显示。</p></section>}

    {!practice && <section className="rw-explore" aria-labelledby="rw-explore-heading"><div className="rw-section-heading"><div><span className="rw-kicker">01 / 探索</span><h2 id="rw-explore-heading">从一个词，理清一组词</h2><p>选词库里的词，比较近义词、派生词与常用搭配。</p></div></div>
      <div className="rw-explore-grid"><div className="rw-source-picker"><label className="rw-search"><Search size={15} /><input type="search" aria-label="搜索词库中的单词" placeholder="搜索词库中的单词" maxLength={100} value={sourceQuery} disabled={blocked} onChange={event => setSourceQuery(event.target.value)} /></label>
        <p className="rw-menu-caption">{sourceQuery ? '匹配的词' : '先从尚未掌握的词开始'}</p>
        {suggestions.length ? <ul className="rw-source-menu">{suggestions.map(item => <li key={item.id}><button type="button" disabled={blocked} className={selected?.id === item.id ? 'is-selected' : ''} aria-pressed={selected?.id === item.id} aria-label={`选择 ${item.word} 作为关联起点`} onClick={() => pickSource(item.lemma)}><span><strong lang="en">{item.word}</strong><StatusBadge status={item.status} /></span><small>{item.meaning_summary || '选中后可查看详细释义'}</small></button></li>)}</ul> : <p className="rw-picker-empty">{vocabulary.length ? '没有匹配的词，换一个拼写试试。' : '词库还没有单词。先在阅读或词库中添加，再来建立关联。'}</p>}
      </div><div className="rw-explore-detail">
        {selected ? <><div className="rw-selected-source"><div><span className="rw-kicker">当前起点</span><div className="rw-selected-word">{wordButton(selected.word)}<StatusBadge status={selected.status} /></div><p>{selected.meaning_summary || '点单词查看中文释义'}</p></div><span className="rw-source-instruction">选择关联类型后<br />点击「发现」获取</span></div>
          {!externallyDisabled && <fieldset className="rw-expansion-fieldset" disabled={blocked}><WordExpansion key={selected.lemma} word={selected.lemma} cache={cache.current} vocabulary={vocabulary} onLearningChanged={onLearningChanged} /></fieldset>}
        </> : <div className="rw-explore-empty"><Layers3 size={30} strokeWidth={1.3} /><h3>先选择一个起点词</h3><p>生成前不会调用 AI。已有结果会优先复用，<br />需要的关联再留下、加入生词库。</p></div>}
      </div></div>
      {Boolean(result.data?.sources.length) && <div className="rw-saved-sources"><span>已积累的起点</span>{result.data!.sources.map(source => <button type="button" key={source.word} disabled={blocked || !findRelationSource(vocabulary, source.word)} title={findRelationSource(vocabulary, source.word) ? source.meaning_summary || source.word : '当前词库中没有这个起点词，可在下方查看已保存的关联'} onClick={() => pickSource(source.word)}><span lang="en">{source.word}</span><small>{source.relation_count}</small></button>)}</div>}
    </section>}

    {syncError && <div className="rw-error" role="alert"><p>反馈已保存，但学习数据刷新失败：{syncError}</p><button type="button" className="text-button" disabled={blocked} onClick={() => void refreshLearning()}>刷新学习数据</button></div>}
    {result.error && !practice && <div className="rw-error" role="alert"><p>关联暂时无法读取：{result.error}</p><button type="button" className="text-button" disabled={blocked} onClick={() => setRetry(value => value + 1)}>重试关联</button></div>}

    {practice && <section className="rw-practice" aria-label="关联回想练习"><div className="rw-section-heading"><div><span className="rw-kicker">关联回想</span><h2 ref={practiceHeading} tabIndex={-1}>{activePractice ? `第 ${practice.index + 1} / ${practice.items.length} 组` : '本轮回想已完成'}</h2></div><button type="button" className="text-button" disabled={blocked} onClick={() => setPractice(null)}><X size={14} />返回关联列表</button></div>
      <p className="rw-practice-note">使用开始时本页的关联快照。本轮自评只在当前页面保留，不改变掌握状态或间隔复习安排。</p>
      {practiceItem ? <><div className="rw-recall-prompt"><span className="tiny-badge">{relationWorkspaceLabels[practiceItem.relation_type]}</span><p>从这个词联想</p><strong lang="en">{practiceItem.source_word}</strong><div className="rw-recall-meaning"><span>想起与它关联的英文词</span><p>{relationRecallMeaning(practiceItem)}</p></div></div>
        {!practice.revealed ? <button type="button" className="button primary rw-reveal" disabled={blocked} onClick={() => setPractice(current => current ? { ...current, revealed: true } : current)}>揭晓关联词<ArrowRight size={16} /></button> : <div className="rw-recall-answer"><div className="rw-pair"><div><span>起点词</span>{wordButton(practiceItem.source_word)}<p>{practiceItem.source.meaning_summary || '点单词查看释义'}</p></div><ArrowRight size={19} /><div><span>关联词</span>{wordButton(practiceItem.target_word)}<p>{practiceItem.meaning}</p></div></div><div className="rw-reason"><span>关联与用法</span><p>{practiceItem.reason || '这条旧记录没有保存用法说明，可通过查词进一步比较。'}</p></div>{rowActions(practiceItem)}<div className="rw-recall-choices"><button type="button" className="button secondary" disabled={blocked} onClick={() => answerPractice(false)}>还需巩固</button><button type="button" className="button primary" disabled={blocked} onClick={() => answerPractice(true)}><Check size={15} />想起来了</button></div></div>}
      </> : <div className="rw-practice-complete"><Check size={30} strokeWidth={1.4} /><p>回想了 <strong>{practice.items.length}</strong> 组，其中 <strong>{practice.missed.length}</strong> 组标记为还需巩固。</p><div>{practice.missed.length > 0 && <button type="button" className="button primary" disabled={blocked} onClick={() => startPractice(practice.missed)}>再练未想起的 {practice.missed.length} 组</button>}<button type="button" className="button secondary" disabled={blocked} onClick={onReview}>去复习到期词<ArrowRight size={15} /></button></div></div>}
    </section>}

    {!practice && <section className="rw-collection" aria-labelledby="rw-saved-heading"><div className="rw-collection-heading"><div><span className="rw-kicker">02 / 积累</span><h2 id="rw-saved-heading" ref={listHeading} tabIndex={-1}>保存的词汇关联</h2><p>保留用法说明，随时比较、回原文，或用中文提示回想英文。</p></div><button type="button" className="button secondary" disabled={blocked || !ready || !practiceItems.length} onClick={() => startPractice(practiceItems)}><Layers3 size={15} />练习本页 {practiceItems.length} 组</button></div>
      <div className="rw-filters"><label className="rw-search"><Search size={15} /><input type="search" aria-label="搜索已保存的关联" placeholder="搜索起点词或关联词" value={search} maxLength={100} disabled={blocked} onChange={event => setSearch(event.target.value)} /></label><select aria-label="筛选关联类型" value={filters.relation} disabled={blocked} onChange={event => { movingPage.current = false; setFilters(current => ({ ...current, relation: event.target.value as StoredRelationType | 'all', page: 1 })); }}><option value="all">全部关联类型</option>{Object.entries(relationWorkspaceLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>
        <label className="rw-checkbox"><input type="checkbox" checked={filters.scope === 'unfamiliar'} disabled={blocked} onChange={event => { movingPage.current = false; setFilters(current => ({ ...current, scope: event.target.checked ? 'unfamiliar' : 'all', page: 1 })); }} />只看尚未掌握的关联词</label>
        <label className="rw-checkbox"><input type="checkbox" checked={filters.include_dismissed} disabled={blocked} onChange={event => { movingPage.current = false; setFilters(current => ({ ...current, include_dismissed: event.target.checked, page: 1 })); }} />显示已略过的关联</label>
        {(search || filters.relation !== 'all' || filters.scope !== 'all' || filters.include_dismissed) && <button type="button" className="text-button" disabled={blocked} onClick={() => { movingPage.current = false; setSearch(''); setFilters(defaultFilters); }}>清除筛选<X size={12} /></button>}
      </div>
      <div className="rw-items" aria-busy={result.loading || (!ready && !result.error && !externallyDisabled)}>
        {(result.loading || (!ready && !result.error && !externallyDisabled)) && <p className={`rw-message${listVisible ? ' rw-background-message' : ''}`} role="status"><LoaderCircle size={16} className="spin" />{listVisible ? '正在刷新关联，保存操作暂时暂停…' : '正在整理词汇关联…'}</p>}
        {ready && !items.length && <div className="rw-collection-empty"><Layers3 size={28} strokeWidth={1.3} /><h3>{filters.q || filters.relation !== 'all' || filters.scope !== 'all' || filters.include_dismissed ? '没有符合条件的关联' : '还没有保存的词汇关联'}</h3><p>{filters.q || filters.relation !== 'all' || filters.scope !== 'all' || filters.include_dismissed ? '换一个单词或筛选条件试试。' : '在上方选一个词，发现关联后主动查看或留下反馈，就会保存在这里。'}</p></div>}
        {listVisible && items.map(item => <article key={item.id} className={`rw-relation${item.useful === false ? ' is-dismissed' : ''}`} aria-label={`${item.source_word} 与 ${item.target_word} 的关联`}><div className="rw-relation-top"><span className="tiny-badge">{relationWorkspaceLabels[item.relation_type]}</span><span className="rw-provenance">{item.provenance === 'llm' ? 'AI 生成 · 请结合语境核对' : item.provenance === 'offline' ? '离线规则推荐' : '旧记录 · 来源信息不全'}</span>{item.useful === false && <span className="rw-dismissed-label">已略过</span>}</div>
          <div className="rw-pair"><div><span>起点词</span><div className="rw-pair-heading">{wordButton(item.source_word)}<StatusBadge status={item.source.status} /></div><p>{item.source.meaning_summary || '点单词查看释义'}</p></div><ArrowRight size={18} aria-hidden="true" /><div><span>关联词</span><div className="rw-pair-heading">{wordButton(item.target_word)}<StatusBadge status={item.target.status} /></div><p>{item.meaning || item.target.meaning_summary || '这条旧记录没有保存关联释义，点单词查看词典。'}</p></div></div>
          <div className="rw-reason"><span>关联与用法</span><p>{item.reason || '这条旧记录没有保存用法说明，可通过查词进一步比较。'}</p></div>
          {item.context && <details className="rw-context"><summary>查看来源句 · {item.context.document_title}</summary><p lang="en">{item.context.sentence}</p></details>}
          {rowActions(item)}
        </article>)}
      </div>
      {listVisible && items.length > 0 && <div className="rw-pagination"><span>共 {result.data!.pagination.total} 组 · 第 {page} / {totalPages} 页</span><nav aria-label="词汇关联分页"><button type="button" aria-label="上一页关联" disabled={feedbackBlocked || page <= 1} onClick={() => changePage(page - 1)}><ChevronLeft size={14} /></button>{relationPageNumbers(page, totalPages).map(value => typeof value === 'number' ? <button type="button" key={value} aria-label={`第 ${value} 页`} aria-current={value === page ? 'page' : undefined} disabled={feedbackBlocked || value === page} onClick={() => changePage(value)}>{value}</button> : <span key={value} aria-hidden="true">…</span>)}<button type="button" aria-label="下一页关联" disabled={feedbackBlocked || page >= totalPages} onClick={() => changePage(page + 1)}><ChevronRight size={14} /></button></nav></div>}
      <p className="rw-footnote">“练习本页”使用未略过且有中文释义的关联，旧记录缺少释义时不参与。浏览、查词与回想不自动修改掌握状态；点击「加入生词」才会加入复习安排。</p>
    </section>}
  </div>;
}
