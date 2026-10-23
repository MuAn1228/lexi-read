import { useEffect, useId, useRef, useState } from 'react';
import { ArrowUpRight, LoaderCircle, Network, RefreshCw } from 'lucide-react';
import { errorMessage, request } from './api';
import RelatedWordCard from './RelatedWordCard';
import type { Vocabulary } from './types';
import { cacheExpansion, expansionCacheKey, expansionLabel, expansionOptions, expansionPath, expansionSourceLabel, isExpansionRecommendation } from './wordExpansion';
import type { ExpansionRecommendation, ExpansionRelation, WordExpansionCache } from './wordExpansion';
import './wordExpansion.css';

const noLearningChange = async () => {};

export default function WordExpansion({ word, cache, vocabulary = [], onLearningChanged }: {
  word: string; cache: WordExpansionCache; vocabulary?: Vocabulary[]; onLearningChanged?: () => Promise<void>;
}) {
  const [relation, setRelation] = useState<ExpansionRelation>('domain_related');
  const [recommendation, setRecommendation] = useState<ExpansionRecommendation | null>(null);
  const [cached, setCached] = useState(false);
  const [loading, setLoading] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const heading = useId(); const panel = useId();
  const selectedOption = expansionOptions.find(option => option.relation === relation)!;

  useEffect(() => {
    ++generation.current; controller.current?.abort(); controller.current = null;
    const saved = cache.get(expansionCacheKey(word, relation)) ?? null;
    setRecommendation(saved); setCached(Boolean(saved)); setLoading(false); setError('');
    return () => { ++generation.current; controller.current?.abort(); controller.current = null; };
  }, [word, relation, cache]);

  useEffect(() => {
    if (!loading) return;
    const started = Date.now(); setSeconds(0);
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [loading]);

  const stop = () => {
    ++generation.current; controller.current?.abort(); controller.current = null; setLoading(false);
  };

  const select = (value: ExpansionRelation) => {
    if (value === relation) return;
    stop(); setRelation(value);
  };

  const generate = async () => {
    if (controller.current) return;
    const abort = new AbortController(); controller.current = abort;
    const attempt = ++generation.current;
    setLoading(true); setSeconds(0); setError('');
    try {
      const result = await request<unknown>(expansionPath(word), {
        method: 'POST', body: JSON.stringify({ relation }), signal: abort.signal,
      });
      if (attempt !== generation.current || abort.signal.aborted) return;
      if (!isExpansionRecommendation(result, relation)) throw new Error('拓展结果暂时无法读取，请重试。');
      setRecommendation(result); setCached(false);
      cacheExpansion(cache, word, relation, result);
    } catch (reason) {
      if (attempt === generation.current && !abort.signal.aborted) setError(errorMessage(reason));
    } finally {
      if (attempt === generation.current && !abort.signal.aborted) { setLoading(false); controller.current = null; }
    }
  };

  return <section className="dictionary-section word-expansion" aria-labelledby={heading}>
    <div className="dictionary-section-heading"><h3 id={heading}><Network size={17} />词汇拓展</h3><span className="dictionary-badge">一次最多 3 个</span></div>
    <p className="word-expansion-intro">从一个词多走一步。选择关联方式，按需生成；想记住的词再加入生词库。</p>
    <div className="word-expansion-options" role="group" aria-label="词汇拓展类型">
      {expansionOptions.map(option => <button type="button" className={`${option.primary ? 'is-primary ' : ''}${relation === option.relation ? 'is-active' : ''}`} key={option.relation}
        aria-pressed={relation === option.relation} aria-controls={panel} onClick={() => select(option.relation)}>{option.label}</button>)}
    </div>
    <div id={panel} className="word-expansion-content" aria-label={`${selectedOption.label}拓展`}>
      <p className="word-expansion-description">{selectedOption.description}</p>
      {!recommendation && !loading && <button type="button" className="button secondary word-expansion-generate" onClick={() => void generate()}>
        {error ? <RefreshCw size={15} /> : <ArrowUpRight size={15} />}{error ? `重新获取${expansionLabel(relation)}` : `发现${expansionLabel(relation)}`}
      </button>}
      {loading && <div className="word-expansion-loading"><p role="status"><LoaderCircle size={16} className="spin" />正在整理{selectedOption.label}…<span>{seconds} 秒</span></p><button type="button" className="text-button" onClick={stop}>停止等待</button></div>}
      {error && <div className="inline-error word-expansion-error" role="alert"><p>{error}</p>{recommendation && <button type="button" className="text-button" disabled={loading} onClick={() => void generate()}><RefreshCw size={13} />重试获取</button>}</div>}
      {recommendation && <>
        <div className="recommendation-meta word-expansion-meta"><span className={`source-badge ${recommendation.source}`}>{expansionSourceLabel(recommendation, cached)}</span>
          <button type="button" className="text-button" disabled={loading} aria-label={`重新生成${selectedOption.label}`} onClick={() => void generate()}><RefreshCw size={13} />重新获取</button></div>
        <div className="word-expansion-cards">{recommendation.items.map(item => <RelatedWordCard key={`${recommendation.batch_id}:${item.word}`} item={item} batchId={recommendation.batch_id}
          status={vocabulary.find(entry => entry.lemma === item.word.toLowerCase() || entry.word.toLowerCase() === item.word.toLowerCase())?.status ?? null}
          disabled={loading} onSaved={onLearningChanged ?? noLearningChange} variant="expansion" />)}</div>
        {!recommendation.items.length && <p className="word-expansion-empty">暂时没有找到合适的{selectedOption.label}。可以换一种关联方式，或稍后重试。</p>}
        {recommendation.warning && <p className="word-expansion-warning" role="status">{recommendation.warning}</p>}
      </>}
      <p className="word-expansion-note">生成和查看不改变掌握状态或复习安排。派生词关注词族关系，近义词请结合具体用法辨别。</p>
    </div>
  </section>;
}
