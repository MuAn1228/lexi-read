import { useEffect, useState } from 'react';
import { ArrowRight, LoaderCircle, Network, RefreshCw } from 'lucide-react';
import { errorMessage, formatDate, request } from './api';
import type { Relations, StoredRelationType, Vocabulary } from './types';

const relationLabels: Record<StoredRelationType, string> = {
  WORD_FAMILY: '同词族', SYNONYM: '近义词', ANTONYM: '反义词',
  CONCEPT: '概念关联', COLLOCATION: '常用搭配', DOMAIN_RELATED: '领域相关',
};
const metrics: { key: keyof Relations['metrics']; label: string; description: string }[] = [
  { key: 'generated_items', label: '生成的推荐', description: '为你准备过的关联词' },
  { key: 'shown_items', label: '已展示', description: '已在阅读中显示的推荐' },
  { key: 'viewed_items', label: '主动查看', description: '你点击展开的关联词' },
  { key: 'added_unknown_items', label: '加入生词', description: '由推荐转为不认识' },
  { key: 'useful_items', label: '觉得有用', description: '最近反馈为有用' },
  { key: 'not_useful_items', label: '觉得没用', description: '最近反馈为没用' },
];

export default function RelationsView({ vocabulary }: { vocabulary: Vocabulary[] }) {
  const [relations, setRelations] = useState<Relations | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let ignore = false;
    const controller = new AbortController();
    setLoading(true); setError(''); setRelations(null);
    request<Relations>('/relations', { signal: controller.signal })
      .then(value => { if (!ignore) setRelations(value); })
      .catch(e => { if (!ignore) setError(errorMessage(e)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; controller.abort(); };
  }, [vocabulary, revision]);

  return <div className="relations-view">
    <div className="dashboard-toolbar"><p>从你主动查看或留下反馈的推荐中，积累词与词的联系。</p><button className="text-button" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={14} className={loading ? 'spin' : ''} />刷新关联</button></div>
    {loading && <div className="loading-copy" role="status"><LoaderCircle size={17} className="spin" />正在整理你的词汇关联…</div>}
    {error && <div className="error-banner" role="alert"><span>词汇关联暂时无法读取：{error}</span><button className="text-button" onClick={() => setRevision(value => value + 1)}><RefreshCw size={14} />重试关联</button></div>}
    {relations && <>
      <section aria-label="关联推荐记录" className="stats-grid relations-stats">{metrics.map(metric => <div className="stat-card" key={metric.key}><div><span>{metric.label}</span></div><strong>{relations.metrics[metric.key]}</strong><span>项</span><p>{metric.description}</p></div>)}</section>
      <p className="dashboard-note">每轮推荐中的单词分别计数，同一个词再次被推荐会单独计入。「有用 / 没用」以每轮推荐的最近反馈为准。以上统计从支持逐词记录的推荐开始，旧推荐未记录的部分不计入。</p>
      <section className="panel relations-panel" aria-labelledby="relations-title">
        <div className="library-heading"><div><h2 id="relations-title">我的词汇关联 <span className="count-badge">{relations.total} 条</span></h2><p>只展示你主动查看或反馈后建立的联系。关联强度随查看和反馈按规则更新，仅供学习参考。</p></div></div>
        {relations.items.length ? <ul className="relation-list">{relations.items.map(item => <li key={item.id} className="relation-card">
          <div className="relation-card-heading"><span className="tiny-badge">{relationLabels[item.relation_type]}</span><time dateTime={item.updated_at}>更新于 {formatDate(item.updated_at)}</time></div>
          <div className="relation-words"><strong>{item.source_word}</strong><ArrowRight size={18} aria-label="关联到" /><strong>{item.target_word}</strong></div>
          <div className="relation-strength"><span>规则关联强度</span><strong>{Math.round(item.weight * 100)}<span> / 100</span></strong></div>
          <div className="progress-track" role="meter" aria-label={`${item.source_word} 到 ${item.target_word} 的规则关联强度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(item.weight * 100)}><div style={{ width: `${item.weight * 100}%` }} /></div>
        </li>)}</ul> : <div className="empty-library"><Network size={32} /><h3>从一次好奇的查看开始</h3><p>在阅读空间发现关联词，主动展开或留下反馈。<br />这些词之间的联系就会出现在这里。</p></div>}
        {relations.items.length > 0 && <p className="relations-list-note">显示最近更新的 {relations.items.length} 条，共 {relations.total} 条；最多展示 200 条。</p>}
      </section>
    </>}
  </div>;
}
