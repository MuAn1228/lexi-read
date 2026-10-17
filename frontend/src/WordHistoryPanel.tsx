import { useEffect, useState } from 'react';
import { Clock3, LoaderCircle, RefreshCw } from 'lucide-react';
import { errorMessage, formatDate, request } from './api';
import type { WordHistory } from './types';
import { StatusBadge } from './WordControls';
import { learningEventLabel } from './eventLabels';

export default function WordHistoryPanel({ lemma, updatedAt, revision }: {
  lemma: string; updatedAt?: string; revision: number;
}) {
  const [history, setHistory] = useState<WordHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    let ignore = false;
    setLoading(true); setError(''); setHistory(null);
    // Reading history is passive: it must never create a LOOKUP or change mastery.
    request<WordHistory>(`/vocabulary/${encodeURIComponent(lemma)}/history`, { signal: controller.signal })
      .then(value => { if (!ignore) setHistory(value); })
      .catch(e => { if (!ignore) setError(errorMessage(e)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; controller.abort(); };
  }, [lemma, updatedAt, revision, retry]);

  const entry = history?.vocabulary;
  const visibleEvents = history?.events.slice(0, expanded ? 20 : 3) ?? [];
  return <section className="word-history" aria-labelledby="word-history-title">
    <div className="section-label"><h3 id="word-history-title"><Clock3 size={14} />这个词的学习记录</h3>{history?.last_mark && <span className="tiny-badge">之前标记过</span>}</div>
    {loading && <div className="loading-copy" role="status"><LoaderCircle size={15} className="spin" />正在读取学习记录…</div>}
    {error && <div className="inline-error" role="alert"><p>学习记录暂时无法读取：{error}</p><button className="text-button" onClick={() => setRetry(value => value + 1)}><RefreshCw size={13} />重试学习记录</button></div>}
    {history && entry && <>
      {history.last_mark && <p className="prior-feedback">你已在原文遇见这个词 <strong>{entry.seen_count}</strong> 次。<br />上一次{learningEventLabel(history.last_mark)}。<time dateTime={history.last_mark.created_at}>{formatDate(history.last_mark.created_at)}</time></p>}
      <dl className="word-history-facts">
        <div><dt>第一次出现</dt><dd>{formatDate(entry.first_seen_at)}</dd></div>
        <div><dt>最近出现</dt><dd>{formatDate(entry.last_seen_at)}</dd></div>
        <div><dt>总出现次数</dt><dd>{entry.seen_count} 次</dd></div>
        <div><dt>查询次数</dt><dd>{entry.lookup_count} 次</dd></div>
        <div><dt>当前掌握状态</dt><dd><StatusBadge status={entry.status} /></dd></div>
        <div><dt>规则掌握度</dt><dd>{entry.mastery_score === null ? '尚未评估' : `${Math.round(entry.mastery_score * 100)}%`}</dd></div>
      </dl>
      <p className="mastery-note">掌握度按你的标记和主动查词简单调整，仅供学习参考。</p>
      {history.events.length > 0 ? <>
        <ol className="word-event-list">{visibleEvents.map(event => <li key={event.id}><span>{learningEventLabel(event)}</span><time dateTime={event.created_at}>{formatDate(event.created_at)}</time></li>)}</ol>
        {history.events.length > 3 && <button className="text-button history-toggle" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? '收起记录' : `查看最近 ${history.events.length} 条记录`}</button>}
      </> : <p className="service-note">还没有主动查词或标记记录。阅读遇见不会自动计为查词。</p>}
    </>}
  </section>;
}
