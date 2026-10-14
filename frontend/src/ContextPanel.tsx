import { useEffect, useState } from 'react';
import { ArrowDownRight, ArrowRight, BookOpen, Check, LoaderCircle, Plus, RefreshCw, Sparkles } from 'lucide-react';
import { errorMessage, request } from './api';
import type { Explanation, Occurrence, Recommendation, Vocabulary, WordStatus } from './types';
import { StatusBadge, WordControls } from './WordControls';

export default function ContextPanel({ word, onMark, saving, onNext, onAdd, vocabulary }: {
  word: Occurrence | null; onMark: (status: WordStatus) => void; saving: boolean;
  onNext: () => void; onAdd: (word: string) => Promise<void>; vocabulary: Vocabulary[];
}) {
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [loading, setLoading] = useState(false);
  const [recommending, setRecommending] = useState(false);
  const [error, setError] = useState('');
  const [recommendError, setRecommendError] = useState('');
  const [retry, setRetry] = useState(0);
  const [relatedRequest, setRelatedRequest] = useState(0);
  const occurrenceId = word?.id;

  useEffect(() => {
    setExplanation(null); setRecommendation(null); setError(''); setRecommendError('');
    setLoading(Boolean(occurrenceId)); setRecommending(false); setRelatedRequest(0);
    if (!occurrenceId) return;
    const controller = new AbortController();
    let ignore = false;
    // Debouncing skips API calls while the user quickly scans and marks words.
    const timer = setTimeout(() => {
      request<Explanation>('/words/analyze', { method: 'POST', body: JSON.stringify({ occurrence_id: occurrenceId }), signal: controller.signal })
        .then(value => { if (!ignore) setExplanation(value); })
        .catch(e => { if (!ignore) setError(errorMessage(e)); })
        .finally(() => { if (!ignore) setLoading(false); });
    }, 300);
    return () => { ignore = true; clearTimeout(timer); controller.abort(); };
  }, [occurrenceId, retry]);

  useEffect(() => {
    if (!occurrenceId || relatedRequest === 0) return;
    const controller = new AbortController();
    let ignore = false;
    setRecommending(true); setRecommendError('');
    request<Recommendation>('/words/recommend', { method: 'POST', body: JSON.stringify({ occurrence_id: occurrenceId }), signal: controller.signal })
      .then(value => { if (!ignore) setRecommendation(value); })
      .catch(e => { if (!ignore) setRecommendError(errorMessage(e)); })
      .finally(() => { if (!ignore) setRecommending(false); });
    return () => { ignore = true; controller.abort(); };
  }, [occurrenceId, relatedRequest]);

  if (!word) return <aside className="context-panel panel context-placeholder"><div className="round-icon"><BookOpen size={26} /></div><h2>每个词，都有它的语境。</h2><p>在原文或单词列表中选择一个词，<br />从当前句子理解它。</p></aside>;

  return <aside className="context-panel panel">
    <div className="panel-heading"><div><Sparkles size={16} /><h2>语境学习</h2></div><button className="text-button" onClick={onNext}>下一个 <ArrowRight size={14} /></button></div>
    <div className="context-content">
      <div className="context-word-heading"><span className="overline">IN THIS CONTEXT</span><StatusBadge status={word.status} /></div>
      <h2 className="current-word">{word.word}</h2><p className="lemma-line">{explanation?.part_of_speech || '英文词汇'}<span>·</span>词元 {explanation?.lemma || word.lemma}</p>
      <WordControls word={word.word} status={word.status} disabled={saving} onMark={onMark} />
      <div className="context-sentence"><span className="quote-mark">“</span><p>{word.sentence}</p></div>
      <section className="meaning-section"><div className="section-label"><span>在这里，它的意思是</span>{explanation && <span className={`source-badge ${explanation.source}`}>{explanation.source === 'llm' ? 'AI 语境释义' : '离线基础释义'}</span>}</div>
        {loading && <div className="loading-copy" role="status"><LoaderCircle size={16} className="spin" />正在结合原文理解这个词…</div>}
        {error && <div className="inline-error" role="alert"><p>{error}</p><button className="text-button" onClick={() => setRetry(v => v + 1)}><RefreshCw size={13} />重新获取</button></div>}
        {explanation && <><h3>{explanation.meaning_in_context}</h3><p>{explanation.simple_explanation}</p>{explanation.warning && <p className="service-note">{explanation.warning}</p>}</>}
      </section>
      <section className="related-section"><div className="section-label"><span><Sparkles size={14} />顺着语境，再走一步</span><span className="tiny-badge">≤ 3 个词</span></div>
        <p className="related-intro">结合这篇文章和你的掌握情况，只挑值得一起学的词。</p>
        {!recommendation && !recommending && <button className="button related-button" onClick={() => setRelatedRequest(v => v + 1)}><ArrowDownRight size={16} />发现关联词</button>}
        {recommending && <div className="loading-copy" role="status"><LoaderCircle size={16} className="spin" />正在筛选适合你的关联词…</div>}
        {recommendError && <div className="inline-error" role="alert"><p>{recommendError}</p><button className="text-button" onClick={() => setRelatedRequest(v => v + 1)}>重试</button></div>}
        {recommendation && <><div className="recommendation-meta"><span className={`source-badge ${recommendation.source}`}>{recommendation.source === 'llm' ? 'AI 个性化推荐' : '离线规则推荐'}</span><button className="text-button" disabled={recommending} onClick={() => setRelatedRequest(v => v + 1)} aria-label="刷新关联推荐"><RefreshCw size={12} /></button></div>
          {recommendation.items.slice(0, 3).map(item => {
            const saved = vocabulary.some(entry => (entry.word.toLowerCase() === item.word.toLowerCase() || entry.lemma === item.word.toLowerCase()) && entry.status !== null);
            return <article className="related-card" key={item.word}><div><h4>{item.word}</h4><button className={`icon-button add-word ${saved ? 'saved' : ''}`} aria-label={saved ? `${item.word} 已加入词库` : `将 ${item.word} 加入待学习`} title={saved ? '已在词库中' : '以「不确定」状态加入词库'} disabled={saving || saved} onClick={() => void onAdd(item.word)}>{saved ? <Check size={15} /> : <Plus size={15} />}</button></div><p className="related-meaning">{item.meaning}</p><p className="related-reason">{item.reason}</p></article>;
          })}
          {!recommendation.items.length && <p className="service-note">暂时没有合适的关联词。继续阅读，积累更多语境。</p>}
          {recommendation.warning && <p className="service-note">{recommendation.warning}</p>}
        </>}
      </section>
    </div>
  </aside>;
}
