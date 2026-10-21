import { Check, CircleHelp, Clock3, Eye, Sparkles, X } from 'lucide-react';
import { formatDate } from './api';
import type { LearningEvent } from './types';
import { learningEventLabel } from './eventLabels';
export { default as VocabularyView } from './VocabularyView';

export function HistoryView({ events }: { events: LearningEvent[] }) {
  return <section className="panel history-panel"><div className="library-heading"><div><h2>每一次反馈，都让学习更懂你。</h2><p>最近的学习记录，来自你的实际阅读与标记。</p></div><span className="count-badge">{events.length} 条</span></div>
    {events.length ? <div className="timeline">{events.map(event => { const Icon = event.event_type === 'LOOKUP' ? Eye : event.event_type === 'VIEW_RELATED' ? Sparkles : event.event_type === 'MARK_KNOWN' ? Check : event.event_type === 'MARK_UNKNOWN' ? X : CircleHelp; return <article className="history-event" key={event.id}><span className={`event-icon ${event.event_type.toLowerCase()}`}><Icon size={17} /></span><div><strong>{event.word}</strong><span>{learningEventLabel(event)}</span></div><time dateTime={event.created_at}>{formatDate(event.created_at)}</time></article>; })}</div> : <div className="empty-library"><Clock3 size={32} /><h3>还没有学习记录</h3><p>查词、标记和探索关联词后，可以在这里回顾。</p></div>}
    <div className="table-footer">持续积累你的学习轨迹<span>记录以服务端返回的最近记录为准</span></div>
  </section>;
}

