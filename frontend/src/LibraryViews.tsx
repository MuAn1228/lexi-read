import { BookMarked, Check, CircleHelp, Clock3, Download, Eye, Search, Sparkles, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { errorMessage, formatDate, request } from './api';
import type { LearningEvent, Vocabulary, WordStatus } from './types';
import { filters } from './WordList';
import type { StatusFilter } from './WordList';
import { StatusBadge, WordControls } from './WordControls';
import { learningEventLabel } from './eventLabels';
import { downloadVocabulary } from './vocabularyCsv';
import { downloadMaimemoWords, maimemoResponse, matchesMaimemoSource, representativeRecord } from './maimemoSources';
import type { MaimemoFilter, MaimemoSources } from './maimemoSources';
import { useDictionary } from './DictionaryPanel';
import { matchesVocabularyQuery } from './dictionary';

export function VocabularyView({ vocabulary, onMark, saving, localMode = false, onOpenMaimemo }: {
  vocabulary: Vocabulary[]; onMark: (word: string, status: WordStatus) => void; saving: boolean;
  localMode?: boolean; onOpenMaimemo?: () => void;
}) {
  const openDictionary = useDictionary();
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<StatusFilter>('ALL');
  const [exportError, setExportError] = useState('');
  const [sources, setSources] = useState<MaimemoSources>({});
  const [sourceError, setSourceError] = useState('');
  const [retrySources, setRetrySources] = useState(0);
  const [sourceFilter, setSourceFilter] = useState<MaimemoFilter>('ALL');
  const [page, setPage] = useState(0);
  useEffect(() => {
    if (!localMode) return;
    const controller = new AbortController();
    setSourceError('');
    request<MaimemoSources>('/integrations/maimemo/sources', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setSources(value); })
      .catch(error => { if (!controller.signal.aborted) setSourceError(errorMessage(error)); });
    return () => controller.abort();
  }, [localMode, retrySources]);
  const marked = vocabulary.filter(word => word.status !== null);
  const results = useMemo(() => vocabulary.filter(word => matchesVocabularyQuery(word, query) && (filter === 'ALL' || (filter === 'UNMARKED' ? word.status === null : word.status === filter)) && matchesMaimemoSource(sources[word.lemma], sourceFilter)), [vocabulary, query, filter, sources, sourceFilter]);
  const pageCount = Math.max(1, Math.ceil(results.length / 50));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = results.slice(currentPage * 50, (currentPage + 1) * 50);
  const difficultCount = results.filter(word => word.status === 'UNKNOWN' || word.status === 'UNCERTAIN').length;
  useEffect(() => setPage(0), [query, filter, sourceFilter]);
  const stats = [{ label: '已标记词汇', count: marked.length, icon: BookMarked, style: 'total' }, { label: '已经掌握', count: marked.filter(w => w.status === 'KNOWN').length, icon: Check, style: 'known' }, { label: '等待认识', count: marked.filter(w => w.status === 'UNKNOWN').length, icon: X, style: 'unknown' }, { label: '需要巩固', count: marked.filter(w => w.status === 'UNCERTAIN').length, icon: CircleHelp, style: 'uncertain' }];
  return <div className="library-view"><div className="stats-grid">{stats.map(stat => <div className={`stat-card studio-spotlight ${stat.style}`} key={stat.label}><div><span>{stat.label}</span><stat.icon size={18} /></div><strong>{stat.count}</strong><span>个单词</span></div>)}</div>
    <section className="panel vocabulary-panel"><div className="library-heading"><div><h2>一点一点，变成你的词汇。</h2><p>中文词义随时查，义项和用法慢慢学。点击单词查看详细释义；未标记的词不会被当作已掌握。</p></div><label className="word-search"><Search size={16} /><input aria-label="搜索个人词库" placeholder="搜索英文或中文释义…" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
      <div className="filter-tabs">{filters.map(item => <button key={item.value} className={filter === item.value ? 'active' : ''} onClick={() => setFilter(item.value)}>{item.label}</button>)}</div>
      {localMode && <div className="maimemo-source-filter"><label>来源与墨墨反馈<select aria-label="筛选墨墨来源" value={sourceFilter} onChange={event => setSourceFilter(event.target.value as MaimemoFilter)}><option value="ALL">全部来源</option><option value="MAIMEMO">墨墨已导入</option><option value="MAIMEMO_WEAK">墨墨待巩固</option></select></label><p>待巩固：上次反馈为忘记 / 模糊，或带有反复遗忘标签；依据最近导入记录筛选，不改变词间自评。</p></div>}
      <div style={{ padding: '0 20px 16px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}><button className="button secondary" disabled={!results.length} onClick={() => {
        setExportError('');
        try { downloadVocabulary(results); } catch { setExportError('导出暂时失败，请重试。'); }
      }}><Download size={15} />导出当前筛选 CSV（{results.length}）</button><button className="button secondary" disabled={!difficultCount} onClick={() => { setExportError(''); try { downloadMaimemoWords(results); } catch (error) { setExportError(errorMessage(error)); } }}><Download size={15} />导出巩固词给墨墨（{difficultCount}）</button>{localMode && <button className="text-button" onClick={onOpenMaimemo}>从墨墨导入</button>}<span className="mastery-note">TXT 包含当前筛选中的“不认识 / 不确定”词，可粘贴到墨墨云词本。</span>{exportError && <p className="modal-error" role="alert">{exportError}</p>}</div>
      {sourceError && <p className="inline-error" role="status">墨墨来源暂未加载：{sourceError}<button className="text-button" onClick={() => setRetrySources(value => value + 1)}>重试读取来源</button></p>}
      <div className="table-scroll"><table><thead><tr><th>单词 / 词元</th><th>中文释义</th><th>掌握状态</th><th>规则掌握度</th>{localMode && <th>墨墨记录</th>}<th>阅读遇见</th><th>查询次数</th><th>首次 / 最近遇见</th><th>调整状态</th></tr></thead><tbody>{visible.map(word => { const source = sources[word.lemma]; const remote = representativeRecord(source); return <tr key={word.id}><td><strong className="vocab-word"><button type="button" className="dictionary-word-button" onClick={event => openDictionary(word.word, event.currentTarget)} aria-label={`查看 ${word.word} 的中文释义`}>{word.word}</button></strong><span className="vocab-lemma">{word.lemma}</span>{word.phonetic && <span className="vocabulary-phonetic">/{word.phonetic.replace(/^\/+|\/+$/g, '')}/</span>}</td><td className="vocabulary-meaning"><p>{word.meaning_summary || '暂无本地释义，可打开详细讲解'}</p><button type="button" className="text-button" onClick={event => openDictionary(word.word, event.currentTarget)} aria-label={`查看 ${word.word} 的详细释义`}>详细释义 ↗</button></td><td><StatusBadge status={word.status} /></td><td>{word.mastery_score === null ? '尚未评估' : `${Math.round(word.mastery_score * 100)}%`}</td>{localMode && <td className="vocabulary-dates">{remote ? <><span className="maimemo-source-label">墨墨 · {remote.study_count > 0 || remote.first_study_date ? `学习 ${remote.study_count} 次` : '尚未开始学习'}</span><span>上次反馈 · {maimemoResponse(remote.last_response)}</span><span>下次 · {remote.next_study_date ? formatDate(remote.next_study_date) : '暂无复习时间'}</span>{source.records.length > 1 && <span>{source.records.length} 个来源词形 · 显示学习次数最多的一项</span>}</> : '—'}</td>}<td>{word.seen_count} 次</td><td>{word.lookup_count} 次</td><td className="vocabulary-dates"><span>首次 · {formatDate(word.first_seen_at)}</span><span>最近 · {formatDate(word.last_seen_at)}</span></td><td><WordControls word={word.word} status={word.status} onMark={status => onMark(word.word, status)} disabled={saving} compact /></td></tr>; })}</tbody></table></div>
      {!results.length && <div className="empty-library"><BookMarked size={32} /><h3>{query || filter !== 'ALL' ? '没有符合条件的词汇' : '你的词汇地图，从阅读开始'}</h3><p>上传英文图片，标记遇到的单词，它们会保存在这里。</p></div>}
      <div className="table-footer">共 {results.length} 个词汇 · 每页 50 个<div className="vocabulary-pagination"><button className="text-button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button><span aria-live="polite">第 {currentPage + 1} / {pageCount} 页</span><button className="text-button" disabled={currentPage + 1 >= pageCount} onClick={() => setPage(currentPage + 1)}>下一页</button></div></div>
    </section></div>;
}

export function HistoryView({ events }: { events: LearningEvent[] }) {
  return <section className="panel history-panel"><div className="library-heading"><div><h2>每一次反馈，都让学习更懂你。</h2><p>最近的学习记录，来自你的实际阅读与标记。</p></div><span className="count-badge">{events.length} 条</span></div>
    {events.length ? <div className="timeline">{events.map(event => { const Icon = event.event_type === 'LOOKUP' ? Eye : event.event_type === 'VIEW_RELATED' ? Sparkles : event.event_type === 'MARK_KNOWN' ? Check : event.event_type === 'MARK_UNKNOWN' ? X : CircleHelp; return <article className="history-event" key={event.id}><span className={`event-icon ${event.event_type.toLowerCase()}`}><Icon size={17} /></span><div><strong>{event.word}</strong><span>{learningEventLabel(event)}</span></div><time dateTime={event.created_at}>{formatDate(event.created_at)}</time></article>; })}</div> : <div className="empty-library"><Clock3 size={32} /><h3>还没有学习记录</h3><p>查词、标记和探索关联词后，可以在这里回顾。</p></div>}
    <div className="table-footer">持续积累你的学习轨迹<span>记录以服务端返回的最近记录为准</span></div>
  </section>;
}

