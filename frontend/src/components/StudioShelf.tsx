import { ArrowUpRight, BookOpen, Leaf, RotateCcw } from 'lucide-react';
import type { Vocabulary } from '../types';
import './StudioShelf.css';

/** A small view into saved learning data. Opening a destination never records progress. */
export default function StudioShelf({ articleCount, vocabulary, disabled, onNavigate }: {
  articleCount: number; vocabulary: Vocabulary[]; disabled: boolean;
  onNavigate: (view: 'library' | 'vocabulary' | 'review') => void;
}) {
  const known = vocabulary.filter(word => word.status === 'KNOWN').length;
  const unfamiliar = vocabulary.filter(word => word.status === 'UNKNOWN' || word.status === 'UNCERTAIN').length;
  const cards = [
    { view: 'library' as const, icon: BookOpen, label: '收藏的文章', value: articleCount, unit: '篇', note: '给好奇心留个位置', tone: 'paper' },
    { view: 'vocabulary' as const, icon: Leaf, label: '已掌握的词', value: known, unit: '个', note: '一点一点，长成积累', tone: 'green' },
    { view: 'review' as const, icon: RotateCcw, label: '待巩固的词', value: unfamiliar, unit: '个', note: '不认识与不确定的词', tone: 'lilac' },
  ];
  return <section className="studio-shelf" aria-label="我的阅读积累">
    {cards.map(({ view, icon: Icon, label, value, unit, note, tone }) => <button type="button" key={view}
      className={`studio-shelf-card studio-spotlight studio-shelf-${tone}`} disabled={disabled}
      data-motion-burst=""
      onClick={() => onNavigate(view)} aria-label={`${label} ${value} ${unit}，${view === 'review' ? '打开生词复习' : view === 'library' ? '打开文章库' : '打开我的词库'}`}>
      <span className="studio-shelf-icon" aria-hidden="true"><span data-motion-magnet=""><Icon size={19} /></span></span>
      <span className="studio-shelf-copy"><span>{label}</span><small>{note}</small></span>
      <span className="studio-shelf-value">{value.toLocaleString('zh-CN')}<small>{unit}</small></span>
      <ArrowUpRight className="studio-shelf-arrow" size={16} aria-hidden="true" />
    </button>)}
  </section>;
}
