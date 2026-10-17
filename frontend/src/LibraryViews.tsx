import { BookMarked, Check, CircleHelp, Clock3, Download, Eye, Search, Sparkles, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { formatDate } from './api';
import type { LearningEvent, Vocabulary, WordStatus } from './types';
import { filters } from './WordList';
import type { StatusFilter } from './WordList';
import { StatusBadge, WordControls } from './WordControls';
import { learningEventLabel } from './eventLabels';
import { downloadVocabulary } from './vocabularyCsv';

export function VocabularyView({ vocabulary, onMark, saving }: {
  vocabulary: Vocabulary[]; onMark: (word: string, status: WordStatus) => void; saving: boolean;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<StatusFilter>('ALL');
  const [exportError, setExportError] = useState('');
  const marked = vocabulary.filter(word => word.status !== null);
  const results = useMemo(() => vocabulary.filter(word => (word.word.toLowerCase().includes(query.toLowerCase()) || word.lemma.toLowerCase().includes(query.toLowerCase())) && (filter === 'ALL' || (filter === 'UNMARKED' ? word.status === null : word.status === filter))), [vocabulary, query, filter]);
  const stats = [{ label: '已标记词汇', count: marked.length, icon: BookMarked, style: 'total' }, { label: '已经掌握', count: marked.filter(w => w.status === 'KNOWN').length, icon: Check, style: 'known' }, { label: '等待认识', count: marked.filter(w => w.status === 'UNKNOWN').length, icon: X, style: 'unknown' }, { label: '需要巩固', count: marked.filter(w => w.status === 'UNCERTAIN').length, icon: CircleHelp, style: 'uncertain' }];
  return <div className="library-view"><div className="stats-grid">{stats.map(stat => <div className={`stat-card ${stat.style}`} key={stat.label}><div><span>{stat.label}</span><stat.icon size={18} /></div><strong>{stat.count}</strong><span>个单词</span></div>)}</div>
    <section className="panel vocabulary-panel"><div className="library-heading"><div><h2>一点一点，变成你的词汇。</h2><p>保留每次阅读遇到的词；未标记的词不会被当作已掌握。规则掌握度仅供学习参考。</p></div><label className="word-search"><Search size={16} /><input aria-label="搜索个人词库" placeholder="搜索词汇或词元…" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
      <div className="filter-tabs">{filters.map(item => <button key={item.value} className={filter === item.value ? 'active' : ''} onClick={() => setFilter(item.value)}>{item.label}</button>)}</div>
      <div style={{ padding: '0 20px 16px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}><button className="button secondary" disabled={!results.length} onClick={() => {
        setExportError('');
        try { downloadVocabulary(results); } catch { setExportError('导出暂时失败，请重试。'); }
      }}><Download size={15} />导出当前筛选 CSV（{results.length}）</button><span className="mastery-note">导出词汇记录，便于自行整理；完整学习历史仍保存在应用中。</span>{exportError && <p className="modal-error" role="alert">{exportError}</p>}</div>
      <div className="table-scroll"><table><thead><tr><th>单词 / 词元</th><th>掌握状态</th><th>规则掌握度</th><th>阅读遇见</th><th>查询次数</th><th>首次 / 最近遇见</th><th>调整状态</th></tr></thead><tbody>{results.map(word => <tr key={word.id}><td><strong className="vocab-word">{word.word}</strong><span className="vocab-lemma">{word.lemma}</span></td><td><StatusBadge status={word.status} /></td><td>{word.mastery_score === null ? '尚未评估' : `${Math.round(word.mastery_score * 100)}%`}</td><td>{word.seen_count} 次</td><td>{word.lookup_count} 次</td><td className="vocabulary-dates"><span>首次 · {formatDate(word.first_seen_at)}</span><span>最近 · {formatDate(word.last_seen_at)}</span></td><td><WordControls word={word.word} status={word.status} onMark={status => onMark(word.word, status)} disabled={saving} compact /></td></tr>)}</tbody></table></div>
      {!results.length && <div className="empty-library"><BookMarked size={32} /><h3>{query || filter !== 'ALL' ? '没有符合条件的词汇' : '你的词汇地图，从阅读开始'}</h3><p>上传英文图片，标记遇到的单词，它们会保存在这里。</p></div>}
      <div className="table-footer">共 {results.length} 个词汇<span>修改会保存到你的词库</span></div>
    </section></div>;
}

export function HistoryView({ events }: { events: LearningEvent[] }) {
  return <section className="panel history-panel"><div className="library-heading"><div><h2>每一次反馈，都让学习更懂你。</h2><p>最近的学习记录，来自你的实际阅读与标记。</p></div><span className="count-badge">{events.length} 条</span></div>
    {events.length ? <div className="timeline">{events.map(event => { const Icon = event.event_type === 'LOOKUP' ? Eye : event.event_type === 'VIEW_RELATED' ? Sparkles : event.event_type === 'MARK_KNOWN' ? Check : event.event_type === 'MARK_UNKNOWN' ? X : CircleHelp; return <article className="history-event" key={event.id}><span className={`event-icon ${event.event_type.toLowerCase()}`}><Icon size={17} /></span><div><strong>{event.word}</strong><span>{learningEventLabel(event)}</span></div><time dateTime={event.created_at}>{formatDate(event.created_at)}</time></article>; })}</div> : <div className="empty-library"><Clock3 size={32} /><h3>还没有学习记录</h3><p>查词、标记和探索关联词后，可以在这里回顾。</p></div>}
    <div className="table-footer">持续积累你的学习轨迹<span>记录以服务端返回的最近记录为准</span></div>
  </section>;
}

