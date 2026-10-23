import { BookMarked, ChevronDown, Download, Search } from 'lucide-react';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { errorMessage, formatDate, request } from './api';
import type { Vocabulary, WordStatus } from './types';
import { filters } from './WordList';
import { StatusBadge, WordControls } from './WordControls';
import { downloadVocabulary } from './vocabularyCsv';
import { downloadMaimemoWords, maimemoResponse, matchesMaimemoSource, representativeRecord } from './maimemoSources';
import type { MaimemoFilter, MaimemoSources } from './maimemoSources';
import { useDictionary } from './DictionaryPanel';
import { matchesVocabularyQuery } from './dictionary';
import VocabularyPagination from './components/VocabularyPagination';
import { useVocabularyOperations, VocabularyGroupTools, VocabularySelectionBar } from './components/VocabularyOperations';
import type { VocabularyBrowsing } from './vocabularyBrowsing';
import './vocabulary.css';

export default function VocabularyView({ vocabulary, saving, loading, localMode, onOpenMaimemo, initialBrowsing, onBrowsingChange, storageWarning, onVocabularyChanged, onBusyChange, onReloadVocabulary }: {
  vocabulary: Vocabulary[]; onMark: (word: string, status: WordStatus) => void; saving: boolean; loading: boolean;
  localMode: boolean; onOpenMaimemo: () => void; initialBrowsing: VocabularyBrowsing;
  onBrowsingChange: (value: VocabularyBrowsing) => void; storageWarning: boolean;
  onVocabularyChanged: (items: Vocabulary[]) => void; onBusyChange?: (busy: boolean) => void;
  onReloadVocabulary?: () => Promise<void>;
}) {
  const openDictionary = useDictionary();
  const [browsing, setBrowsing] = useState(initialBrowsing);
  const { query, filter, sourceFilter, page, expanded } = browsing;
  const [exportError, setExportError] = useState('');
  const [sources, setSources] = useState<MaimemoSources>({});
  const [sourceError, setSourceError] = useState('');
  const [sourcesLoaded, setSourcesLoaded] = useState(false);
  const [retrySources, setRetrySources] = useState(0);
  const [sortGroups, setSortGroups] = useState<Map<number, boolean> | null>(null);
  const operations = useVocabularyOperations({ vocabulary, saving, onVocabularyChanged, onBusyChange, onReloadVocabulary,
    onBeforeChange: () => setSortGroups(current => current ?? new Map(vocabulary.map(item => [item.id, item.status === 'KNOWN'] as const))) });
  const groupIds = useMemo(() => operations.groupFilter === null ? null : new Set(operations.groups.find(group => group.id === operations.groupFilter)?.vocabulary_ids ?? []), [operations.groupFilter, operations.groups]);
  const list = useRef<HTMLUListElement>(null);
  const scrollY = useRef(initialBrowsing.scrollY);
  const anchor = useRef(initialBrowsing.anchorId !== undefined && initialBrowsing.anchorOffset !== undefined
    ? { id: initialBrowsing.anchorId, offset: initialBrowsing.anchorOffset } : null);
  const restoring = useRef(true);
  const scrollToList = useRef(false);
  const latest = useRef(browsing);
  latest.current = browsing;
  const captureAnchor = useCallback(() => {
    if (restoring.current || !list.current) return;
    anchor.current = null;
    for (const entry of list.current.children) {
      const bounds = entry.getBoundingClientRect();
      if (bounds.bottom > 0 && bounds.top < window.innerHeight) {
        anchor.current = { id: Number((entry as HTMLElement).dataset.wordId), offset: bounds.top };
        break;
      }
    }
  }, []);
  const persist = useCallback(() => {
    captureAnchor();
    onBrowsingChange({ ...latest.current, scrollY: Math.round(scrollY.current),
      anchorId: anchor.current?.id, anchorOffset: anchor.current?.offset });
  }, [captureAnchor, onBrowsingChange]);

  useEffect(() => {
    if (!localMode) return;
    const controller = new AbortController();
    setSourceError('');
    request<MaimemoSources>('/integrations/maimemo/sources', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) { setSources(value); setSourcesLoaded(true); } })
      .catch(error => { if (!controller.signal.aborted) setSourceError(errorMessage(error)); });
    return () => controller.abort();
  }, [localMode, retrySources]);

  // Group globally before pagination. Freeze groups on the first edit until this view closes.
  const results = useMemo(() => vocabulary.filter(word => matchesVocabularyQuery(word, query)
    && (filter === 'ALL' || (filter === 'UNMARKED' ? word.status === null : word.status === filter))
    && (!groupIds || groupIds.has(word.id))
    && matchesMaimemoSource(sources[word.lemma], sourceFilter)).sort((a, b) =>
      Number(sortGroups?.get(b.id) ?? (b.status === 'KNOWN')) - Number(sortGroups?.get(a.id) ?? (a.status === 'KNOWN'))
      || b.id - a.id), [vocabulary, query, filter, sources, sourceFilter, sortGroups, groupIds]);
  // Expanded source records affect the height above a saved position, even with no source filter.
  const awaitingSavedRecords = restoring.current && localMode && expanded.length > 0 && !sourcesLoaded && !sourceError;
  const ready = !loading && (!localMode || sourceFilter === 'ALL' || sourcesLoaded) && !awaitingSavedRecords;
  const pageCount = Math.max(1, Math.ceil(results.length / 50));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = results.slice(currentPage * 50, (currentPage + 1) * 50);
  const difficultCount = results.filter(word => word.status === 'UNKNOWN' || word.status === 'UNCERTAIN').length;
  const stats = [{ label: '全部词汇', count: vocabulary.length, style: 'total' }, { label: '已掌握', count: vocabulary.filter(w => w.status === 'KNOWN').length, style: 'known' }, { label: '不认识', count: vocabulary.filter(w => w.status === 'UNKNOWN').length, style: 'unknown' }, { label: '不确定', count: vocabulary.filter(w => w.status === 'UNCERTAIN').length, style: 'uncertain' }];

  useLayoutEffect(() => {
    // Wait for real vocabulary and, if selected, Maimemo sources before clamping a saved page.
    if (!ready) return;
    if (restoring.current && anchor.current) {
      const index = results.findIndex(word => word.id === anchor.current?.id);
      if (index >= 0) {
        const anchorPage = Math.floor(index / 50);
        if (page !== anchorPage) {
          setBrowsing(value => ({ ...value, page: anchorPage }));
          return;
        }
      } else anchor.current = null;
    }
    if (page !== currentPage) {
      scrollY.current = 0;
      setBrowsing(value => ({ ...value, page: currentPage, expanded: [] }));
      return;
    }
    if (scrollToList.current) {
      list.current?.focus({ preventScroll: true });
      list.current?.scrollIntoView({ block: 'start', behavior: 'instant' });
      scrollY.current = window.scrollY;
      scrollToList.current = false;
    } else if (restoring.current) {
      const entry = anchor.current && list.current?.querySelector<HTMLElement>(`[data-word-id="${anchor.current.id}"]`);
      const top = entry && anchor.current ? window.scrollY + entry.getBoundingClientRect().top - anchor.current.offset : scrollY.current;
      window.scrollTo({ top, behavior: 'instant' });
      scrollY.current = window.scrollY;
    }
    restoring.current = false;
    persist();
  }, [ready, page, currentPage, results, persist]);

  useEffect(() => { if (!restoring.current) persist(); }, [browsing, persist]);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      if (restoring.current) return;
      scrollY.current = window.scrollY;
      captureAnchor();
      clearTimeout(timer);
      timer = setTimeout(persist, 150);
    };
    const onPageHide = () => { if (!restoring.current) { scrollY.current = window.scrollY; persist(); } };
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('pagehide', onPageHide);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('pagehide', onPageHide);
      if (!restoring.current) persist();
    };
  }, [captureAnchor, persist]);

  const changeFilter = (values: Partial<VocabularyBrowsing>) => {
    if (operations.disabled) return;
    operations.clearSelection('筛选范围已改变，已清除之前的选择。');
    anchor.current = null;
    restoring.current = false;
    setBrowsing(value => ({ ...value, ...values, page: 0, expanded: [], anchorId: undefined, anchorOffset: undefined }));
  };
  const changePage = (next: number) => {
    anchor.current = null;
    restoring.current = false;
    scrollToList.current = true;
    setBrowsing(value => ({ ...value, page: next, expanded: [], anchorId: undefined, anchorOffset: undefined }));
  };
  const markWord = (word: Vocabulary, status: WordStatus) => { void operations.batch([word], status); };

  return <div className="library-view">
    <dl className="vocabulary-summary">{stats.map(stat => <div className={stat.style} key={stat.label}><dt>{stat.label}</dt><dd>{stat.count}</dd></div>)}</dl>
    <section className="panel vocabulary-panel">
      <div className="library-heading"><div><h2>词汇清单</h2><p>点击查看释义，勾选后可批量标记或分组。标注时保持原位。</p></div><label className="word-search"><Search size={16} /><input aria-label="搜索个人词库" placeholder="搜索英文或中文释义…" disabled={operations.disabled} maxLength={1000} value={query} onChange={event => changeFilter({ query: event.target.value })} /></label></div>
      <div className="filter-tabs" aria-label="筛选掌握状态">{filters.map(item => <button key={item.value} disabled={operations.disabled} className={filter === item.value ? 'active' : ''} aria-pressed={filter === item.value} onClick={() => changeFilter({ filter: item.value })}>{item.label}</button>)}{(query || filter !== 'ALL' || sourceFilter !== 'ALL' || operations.groupFilter !== null) && <button className="vocabulary-clear-filters" disabled={operations.disabled} onClick={() => { operations.setGroupFilter(null); changeFilter({ query: '', filter: 'ALL', sourceFilter: 'ALL' }); }}>清除筛选</button>}</div>
      {localMode && <div className="maimemo-source-filter"><label>来源<select aria-label="筛选墨墨来源" disabled={operations.disabled} value={sourceFilter} onChange={event => changeFilter({ sourceFilter: event.target.value as MaimemoFilter })}><option value="ALL">全部来源</option><option value="MAIMEMO">墨墨已导入</option><option value="MAIMEMO_WEAK">墨墨待巩固</option></select></label>{sourceFilter === 'MAIMEMO_WEAK' && <p>依据最近导入的忘记 / 模糊反馈或反复遗忘标签筛选，不改变词间自评。</p>}</div>}
      <VocabularyGroupTools operations={operations} onFilter={id => { changeFilter({}); operations.setGroupFilter(id); }} />
      <details className="vocabulary-tools"><summary><Download size={14} aria-hidden="true" />导入与导出<ChevronDown size={14} aria-hidden="true" /></summary><div className="vocabulary-tool-actions"><button className="button secondary" disabled={!ready || !results.length} onClick={() => { setExportError(''); try { downloadVocabulary(results); } catch { setExportError('导出暂时失败，请重试。'); } }}><Download size={15} />导出当前筛选 CSV（{results.length}）</button><button className="button secondary" disabled={!ready || !difficultCount} onClick={() => { setExportError(''); try { downloadMaimemoWords(results); } catch (error) { setExportError(errorMessage(error)); } }}><Download size={15} />导出巩固词给墨墨（{difficultCount}）</button>{localMode && <button className="text-button" disabled={operations.disabled} onClick={onOpenMaimemo}>从墨墨导入</button>}<p className="mastery-note">TXT 包含当前筛选中的“不认识 / 不确定”词，可粘贴到墨墨云词本。</p>{exportError && <p className="modal-error" role="alert">{exportError}</p>}</div></details>
      {storageWarning && <p className="vocabulary-storage-note" role="status">浏览器存储不可用，本次打开期间仍会保留筛选和页码；刷新后无法恢复浏览位置。</p>}
      {sourceError && <p className="inline-error vocabulary-source-error" role="status">墨墨来源暂未加载：{sourceError}<button className="text-button" onClick={() => setRetrySources(value => value + 1)}>重试读取来源</button></p>}
      <div className="vocabulary-list-heading"><span>单词与释义</span><span>已掌握优先 · 同组最新在前</span></div>
      <VocabularySelectionBar operations={operations} visible={visible} ready={ready} />
      <ul className="vocabulary-list" aria-label="词库单词列表" tabIndex={-1} ref={list} aria-busy={!ready}>
        {ready && visible.map(word => {
          const source = sources[word.lemma];
          const remote = representativeRecord(source);
          const isExpanded = expanded.includes(word.id);
          return <li className={`vocabulary-entry ${operations.selected.has(word.id) ? 'is-selected' : ''}`} key={word.id} data-word-id={word.id}>
            <label className="vocabulary-select-word"><input type="checkbox" aria-label={`选择 ${word.word}`} checked={operations.selected.has(word.id)} disabled={operations.disabled} onChange={event => operations.select([word.id], event.target.checked)} /></label>
            <div className="vocabulary-entry-main">
              <div className="vocabulary-word-block"><div className="vocabulary-word-heading"><h3><button type="button" className="dictionary-word-button" onClick={event => openDictionary(word.word, event.currentTarget)} aria-label={`查看 ${word.word} 的中文释义`}>{word.word}</button></h3><StatusBadge status={word.status} /></div>{word.phonetic && <span className="vocabulary-phonetic">/{word.phonetic.replace(/^\/+|\/+$/g, '')}/</span>}{word.lemma !== word.word.toLowerCase() && <span className="vocab-lemma">词元 · {word.lemma}</span>}</div>
              <div className="vocabulary-entry-meaning"><p>{word.meaning_summary || '暂无本地释义，可打开详细讲解'}</p><button type="button" className="text-button" onClick={event => openDictionary(word.word, event.currentTarget)} aria-label={`查看 ${word.word} 的详细释义`}>详细释义 ↗</button></div>
              <div className="vocabulary-entry-actions"><span>调整状态</span><WordControls word={word.word} status={word.status} onMark={status => markWord(word, status)} disabled={operations.disabled || loading} compact showLabels /></div>
            </div>
            <button type="button" className="vocabulary-records-toggle" aria-label={`${word.word} 的学习记录`} aria-expanded={isExpanded} aria-controls={`vocabulary-records-${word.id}`} onClick={() => setBrowsing(value => ({ ...value, expanded: isExpanded ? value.expanded.filter(id => id !== word.id) : [...value.expanded, word.id] }))}>学习记录<ChevronDown size={14} aria-hidden="true" /></button>
            {isExpanded && <div className="vocabulary-records" id={`vocabulary-records-${word.id}`}>
              <dl className="vocabulary-record-grid"><div><dt>规则掌握度</dt><dd>{word.mastery_score === null ? '尚未评估' : `${Math.round(word.mastery_score * 100)}%`}</dd></div><div><dt>阅读遇见</dt><dd>{word.seen_count} 次</dd></div><div><dt>查询次数</dt><dd>{word.lookup_count} 次</dd></div><div><dt>首次遇见</dt><dd>{formatDate(word.first_seen_at)}</dd></div><div><dt>最近遇见</dt><dd>{formatDate(word.last_seen_at)}</dd></div></dl>
              {localMode && <div className="vocabulary-maimemo-record"><strong>墨墨记录</strong>{remote ? <><span>{remote.study_count > 0 || remote.first_study_date ? `学习 ${remote.study_count} 次` : '尚未开始学习'}</span><span>上次反馈 · {maimemoResponse(remote.last_response)}</span><span>下次复习 · {remote.next_study_date ? formatDate(remote.next_study_date) : '暂无复习时间'}</span>{source.records.length > 1 && <span>{source.records.length} 个来源词形，显示学习次数最多的一项</span>}</> : <span>{sourcesLoaded ? '尚未从墨墨导入这个词' : '墨墨记录暂未加载'}</span>}</div>}
            </div>}
          </li>;
        })}
      </ul>
      {!ready ? <div className="empty-library" role="status"><p>{sourceError && !loading ? '请重试读取来源，或切换为全部来源。' : '正在读取词库…'}</p></div> : !results.length && <div className="empty-library"><BookMarked size={32} /><h3>{query || filter !== 'ALL' || sourceFilter !== 'ALL' || operations.groupFilter !== null ? '没有符合条件的词汇' : '还没有收录单词'}</h3><p>{query || filter !== 'ALL' || sourceFilter !== 'ALL' || operations.groupFilter !== null ? '试试其他搜索词或筛选条件。' : '导入英文材料并标记单词，或从墨墨导入已有词库。'}</p></div>}
      {ready && <div className="table-footer vocabulary-footer"><div className="vocabulary-page-summary"><span>共 {results.length} 个词汇 · 每页 50 个</span><span aria-live="polite">第 {currentPage + 1} / {pageCount} 页</span></div><VocabularyPagination currentPage={currentPage} pageCount={pageCount} onPageChange={changePage} /></div>}
    </section>
  </div>;
}
