import { ListFilter, Search } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { Occurrence, WordStatus } from './types';
import { StatusBadge, WordControls } from './WordControls';

export type StatusFilter = 'ALL' | 'UNMARKED' | WordStatus;
export const filters: { value: StatusFilter; label: string }[] = [
  { value: 'ALL', label: '全部' }, { value: 'UNMARKED', label: '未标记' }, { value: 'UNKNOWN', label: '不认识' }, { value: 'UNCERTAIN', label: '不确定' }, { value: 'KNOWN', label: '已掌握' },
];

export default function WordList({ words, selectedId, query, setQuery, filter, setFilter, total, onSelect, onMark, saving }: {
  words: Occurrence[]; selectedId: number | null; query: string; setQuery: (value: string) => void;
  filter: StatusFilter; setFilter: (value: StatusFilter) => void; total: number;
  onSelect: (word: Occurrence) => void; onMark: (word: Occurrence, status: WordStatus) => void; saving: boolean;
}) {
  const rows = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = rows.current;
    const selected = container?.querySelector<HTMLElement>('.word-row.selected');
    if (!container || !selected) return;
    const viewport = container.getBoundingClientRect();
    const row = selected.getBoundingClientRect();
    // Scroll this pane only; selecting an image word must not move the page.
    if (row.top < viewport.top) container.scrollTop -= viewport.top - row.top;
    else if (row.bottom > viewport.bottom) container.scrollTop += row.bottom - viewport.bottom;
  }, [selectedId, words]);
  return <section className="word-list panel">
    <div className="panel-heading"><div><ListFilter size={16} /><h2>本页单词</h2><span className="count-badge">{total}</span></div></div>
    <div className="word-search"><Search size={15} /><input aria-label="搜索本页单词" placeholder="查找单词…" value={query} onChange={event => setQuery(event.target.value)} /></div>
    <div className="filter-tabs" aria-label="筛选单词状态">{filters.map(item => <button key={item.value} className={filter === item.value ? 'active' : ''} aria-pressed={filter === item.value} onClick={() => setFilter(item.value)}>{item.label}</button>)}</div>
    <div ref={rows} className="word-list-rows">{words.map((word, index) => <div key={word.id} className={`word-row ${selectedId === word.id ? 'selected' : ''}`}>
      <button className="word-select" aria-pressed={selectedId === word.id} onClick={() => onSelect(word)}><span className="word-index">{String(index + 1).padStart(2, '0')}</span><span><strong>{word.word}</strong><StatusBadge status={word.status} /></span></button>
      <WordControls word={word.word} status={word.status} compact disabled={saving} onMark={status => onMark(word, status)} />
    </div>)}{!words.length && <div className="empty-small"><Search size={24} /><p>没有符合条件的单词</p><button className="text-button" onClick={() => { setQuery(''); setFilter('ALL'); }}>清除筛选</button></div>}</div>
    <div className="keyboard-tip"><kbd>1</kbd> 认识 <kbd>2</kbd> 不认识 <kbd>3</kbd> 不确定<span>标记后自动跳到下一个词</span></div>
  </section>;
}
