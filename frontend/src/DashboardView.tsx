import { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, BarChart3, BookOpen, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { errorMessage, formatDate, request } from './api';
import type { Dashboard, DashboardItem, Vocabulary } from './types';
import { StatusBadge } from './WordControls';
import WordHistoryPanel from './WordHistoryPanel';
import { MotionNumber } from './MotionPrimitives';

type GroupKey = 'recent_frequent_unknown' | 'repeated_lookups' | 'recent_unknown' | 'longstanding_unknown' | 'lowest_mastery' | 'most_seen_unknown' | 'most_queried';
const groups: { key: GroupKey; title: string; description: string; metric: (item: DashboardItem) => string }[] = [
  { key: 'recent_frequent_unknown', title: '最近常遇见的生词', description: '近 30 天加入的文章中，出现最多且仍不熟悉的词。', metric: item => `近 30 天遇见 ${item.recent_seen_count} 次` },
  { key: 'repeated_lookups', title: '反复查过的词', description: '累计查询至少 2 次，包括已掌握的词。', metric: item => `累计查询 ${item.vocabulary.lookup_count} 次` },
  { key: 'recent_unknown', title: '这周新发现的生词', description: '近 7 天首次标记为不认识或不确定，且目前仍不熟悉。', metric: item => `首次标记 · ${formatDate(item.first_unfamiliar_at)}` },
  { key: 'longstanding_unknown', title: '还需要一点耐心的词', description: '首次标记为不认识或不确定已满 30 天，目前仍不熟悉。', metric: item => `首次标记 · ${formatDate(item.first_unfamiliar_at)}` },
  { key: 'lowest_mastery', title: '规则掌握度较低', description: '按规则掌握度从低到高排列；尚未评估的词不参与排序。', metric: item => `规则掌握度 ${Math.round((item.vocabulary.mastery_score ?? 0) * 100)}%` },
  { key: 'most_seen_unknown', title: '总是遇见，还没记牢', description: '全部阅读材料中，累计出现最多且仍不熟悉的词。', metric: item => `累计遇见 ${item.vocabulary.seen_count} 次` },
  { key: 'most_queried', title: '查得最多的词', description: '按累计查询次数排列，涵盖所有掌握状态。', metric: item => `累计查询 ${item.vocabulary.lookup_count} 次` },
];

export default function DashboardView({ vocabulary }: { vocabulary: Vocabulary[] }) {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<Vocabulary | null>(null);
  const historyHeading = useRef<HTMLHeadingElement>(null);
  const historyOpener = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    let ignore = false;
    const controller = new AbortController();
    setLoading(true); setError('');
    request<Dashboard>('/dashboard', { signal: controller.signal })
      .then(value => { if (!ignore) setDashboard(value); })
      .catch(e => { if (!ignore) setError(errorMessage(e)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; controller.abort(); };
  }, [vocabulary, revision]);
  useEffect(() => {
    if (selected) {
      historyHeading.current?.focus({ preventScroll: true });
      historyHeading.current?.scrollIntoView({ behavior: 'auto', block: 'nearest' });
    }
  }, [selected]);
  const entry = vocabulary.find(word => word.lemma === selected?.lemma) ?? selected;
  const summary = dashboard?.summary;
  return <div className="dashboard-view">
    <div className="dashboard-toolbar"><p>从你的阅读、标记和查词记录中，找到下一步。</p><div className="dashboard-toolbar-actions">{loading && <span className="dashboard-refresh-status" role="status"><LoaderCircle size={13} className="spin" />{dashboard ? '正在更新，暂显示上次结果' : '正在整理学习概览…'}</span>}<button className="text-button" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={14} className={loading ? 'spin' : ''} />刷新概览</button></div></div>
    {loading && !dashboard && <div className="dashboard-skeleton" aria-hidden="true"><div className="dashboard-skeleton-stats">{Array.from({ length: 5 }, (_, index) => <i key={index} />)}</div><div className="dashboard-skeleton-rows">{[0, 1].map(index => <div key={index}><i /><i /><i /><i /></div>)}</div></div>}
    {error && <div className="error-banner" role="alert"><span>学习概览暂时无法读取：{error}</span><button className="text-button" onClick={() => setRevision(value => value + 1)}><RefreshCw size={14} />重试概览</button></div>}
    {dashboard && summary && <>
      <div className="stats-grid dashboard-stats">{[
        { label: '词库总量', value: summary.total, style: 'total' },
        { label: '已掌握', value: summary.known, style: 'known' },
        { label: '不认识', value: summary.unknown, style: 'unknown' },
        { label: '不确定', value: summary.uncertain, style: 'uncertain' },
        { label: '未标记', value: summary.unassessed, style: 'total' },
      ].map(stat => <div key={stat.label} className={`stat-card studio-spotlight ${stat.style}`}><div><span>{stat.label}</span></div><strong><MotionNumber value={stat.value} /></strong><span>个词</span></div>)}</div>
      <section className="panel dashboard-profile" aria-labelledby="dashboard-profile-title">
        <div className="dashboard-profile-copy"><h2 id="dashboard-profile-title"><BarChart3 size={18} />你的学习画像</h2><p>已标记 {dashboard.profile.assessed_count} 个词，占词库的 {Math.round(dashboard.profile.assessment_coverage * 100)}%。</p><div className="progress-track" role="meter" aria-label="词库标记比例" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(dashboard.profile.assessment_coverage * 100)}><div style={{ width: `${dashboard.profile.assessment_coverage * 100}%` }} /></div><p>{summary.scored_count} 个词有规则掌握度，{dashboard.profile.unscored_count} 个尚未评估。分数仅供学习参考。</p></div>
        <dl className="dashboard-activity"><div><dt>阅读材料</dt><dd><MotionNumber value={summary.document_count} /><span> 篇</span></dd></div><div><dt>近 7 天新生词</dt><dd><MotionNumber value={summary.new_unknown_7d} /><span> 个</span></dd></div><div><dt>近 7 天查询</dt><dd><MotionNumber value={summary.lookups_7d} /><span> 次</span></dd></div></dl>
      </section>
      <p className="dashboard-note">下面的「生词 / 不熟悉」包含不认识和不确定；每组最多显示 10 个词。点击单词可回看学习记录。</p>
      {entry && <div className="dashboard-detail-reveal"><div><section className="panel dashboard-word-detail"><div className="dashboard-detail-heading"><div><p className="overline">WORD HISTORY</p><h2 ref={historyHeading} tabIndex={-1}>{entry.word}<span>{entry.lemma !== entry.word ? ` · ${entry.lemma}` : ''}</span></h2></div><button className="icon-button" aria-label="关闭单词学习记录" onClick={() => { setSelected(null); historyOpener.current?.focus({ preventScroll: true }); }}><X size={18} /></button></div><WordHistoryPanel key={entry.lemma} lemma={entry.lemma} updatedAt={entry.updated_at} revision={revision} /></section></div></div>}
      {summary.total === 0 ? <section className="panel empty-library"><BookOpen size={32} /><h3>从第一篇阅读开始积累</h3><p>添加阅读材料，标记不熟悉的词，学习概览就会在这里慢慢形成。</p></section> : <div className="dashboard-groups">{groups.map((group, index) => <section className="panel dashboard-group" key={group.key} aria-labelledby={`dashboard-${group.key}`}><div className="dashboard-group-heading"><span className="dashboard-group-index" aria-hidden="true">{String(index + 1).padStart(2, '0')} / 学习档案</span><h2 id={`dashboard-${group.key}`}>{group.title}</h2><p>{group.description}</p></div>{dashboard[group.key].length ? <ol className="dashboard-word-list">{dashboard[group.key].map(item => <li key={item.vocabulary.id}><button className={selected?.lemma === item.vocabulary.lemma ? 'selected' : ''} aria-label={`查看 ${item.vocabulary.word} 的学习记录`} onClick={event => { historyOpener.current = event.currentTarget; setSelected(item.vocabulary); }}><span className="dashboard-word"><strong>{item.vocabulary.word}</strong><StatusBadge status={item.vocabulary.status} /></span><span className="dashboard-word-metric">{group.metric(item)}<ArrowUpRight size={14} aria-hidden="true" /></span></button></li>)}</ol> : <p className="dashboard-group-empty">暂时没有符合条件的词。</p>}</section>)}</div>}
      <p className="dashboard-note">近 7 天查询只统计新版本中明确的主动查词，不包含旧版本中无法确认的记录。累计查询保留了旧版本的历史计数。浏览概览和学习记录不会增加查询次数。</p>
    </>}
  </div>;
}
