import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownRight, ArrowRight, BookOpen, LoaderCircle, RefreshCw, Sparkles } from 'lucide-react';
import { errorMessage, request } from './api';
import type { Explanation, Occurrence, Recommendation, Vocabulary, WordStatus } from './types';
import { StatusBadge, WordControls } from './WordControls';
import WordHistoryPanel from './WordHistoryPanel';
import RelatedWordCard from './RelatedWordCard';
import SentencePanel from './SentencePanel';
import { useDictionary } from './DictionaryPanel';
import type { SentenceAnalysisCache } from './sentenceAnalysis';
import PronunciationButton, { SpeechSettings } from './PronunciationButton';

export default function ContextPanel({ word, onMark, saving, onNext, vocabulary, onLearningChanged, sentenceCache, sentencePageStart, sentenceKey }: {
  word: Occurrence | null; onMark: (status: WordStatus) => void; saving: boolean;
  onNext: () => void; vocabulary: Vocabulary[];
  onLearningChanged: () => Promise<void>;
  sentenceCache?: SentenceAnalysisCache; sentencePageStart?: number; sentenceKey?: string;
}) {
  const openDictionary = useDictionary();
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const [loading, setLoading] = useState(false);
  const [recommending, setRecommending] = useState(false);
  const [error, setError] = useState('');
  const [recommendError, setRecommendError] = useState('');
  const [retry, setRetry] = useState(0);
  const [relatedRequest, setRelatedRequest] = useState(0);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [syncError, setSyncError] = useState('');
  const occurrenceId = word?.id;
  const active = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const feedbackSaved = useCallback(async () => {
    await onLearningChanged();
    if (active.current) setHistoryRevision(value => value + 1);
  }, [onLearningChanged]);

  useEffect(() => {
    setExplanation(null); setRecommendation(null); setError(''); setRecommendError('');
    setLoading(Boolean(occurrenceId && retry)); setRecommending(false); setRelatedRequest(0);
    // Passive navigation must not become a lookup or lower future mastery scores.
    if (!occurrenceId || retry === 0) return;
    const controller = new AbortController();
    let ignore = false;
    // Debouncing skips API calls while the user quickly scans and marks words.
    const timer = setTimeout(() => {
      request<Explanation>('/words/analyze', { method: 'POST', body: JSON.stringify({ occurrence_id: occurrenceId }), signal: controller.signal })
        .then(value => {
          if (ignore) return;
          setExplanation(value); setHistoryRevision(revision => revision + 1); setSyncError('');
          void onLearningChanged().catch(e => { if (!ignore) setSyncError(errorMessage(e)); });
        })
        .catch(e => { if (!ignore) setError(errorMessage(e)); })
        .finally(() => { if (!ignore) setLoading(false); });
    }, 300);
    return () => { ignore = true; clearTimeout(timer); controller.abort(); };
  }, [occurrenceId, retry, onLearningChanged]);

  useEffect(() => {
    if (!occurrenceId || relatedRequest === 0) return;
    const controller = new AbortController();
    let ignore = false;
    setRecommending(true); setRecommendError('');
    request<Recommendation>('/words/recommend', { method: 'POST', body: JSON.stringify({ occurrence_id: occurrenceId }), signal: controller.signal })
      .then(value => {
        if (ignore) return;
        setRecommendation(value); setHistoryRevision(revision => revision + 1); setSyncError('');
        void onLearningChanged().catch(e => { if (!ignore) setSyncError(errorMessage(e)); });
      })
      .catch(e => { if (!ignore) setRecommendError(errorMessage(e)); })
      .finally(() => { if (!ignore) setRecommending(false); });
    return () => { ignore = true; controller.abort(); };
  }, [occurrenceId, relatedRequest, onLearningChanged]);

  if (!word) return <aside className="context-panel panel context-placeholder"><div className="round-icon"><BookOpen size={26} /></div><h2>每个词，都有它的语境。</h2><p>在原文或单词列表中选择一个词，<br />从当前句子理解它。</p></aside>;

  return <aside className="context-panel panel">
    <div className="panel-heading"><div><Sparkles size={16} /><h2>语境学习</h2></div><button className="text-button" onClick={onNext}>下一个 <ArrowRight size={14} /></button></div>
    <div className="context-content">
      <div className="context-word-heading"><span className="overline">IN THIS CONTEXT</span><StatusBadge status={word.status} /></div>
      <div className="pronunciation-word-heading"><h2 className="current-word">{word.word}</h2><PronunciationButton text={word.word} label="朗读单词" speechKey={`context-word:${word.id}`} prepareOnMount compact /></div><p className="lemma-line">{explanation?.part_of_speech || '英文词汇'}<span>·</span>词元 {explanation?.lemma || word.lemma}</p>
      <SpeechSettings />
      <button type="button" className="button secondary context-dictionary-button" onClick={event => openDictionary(word.word, event.currentTarget)}><BookOpen size={15} />中文词典与详细讲解</button>
      <WordControls word={word.word} status={word.status} disabled={saving} onMark={onMark} />
      <div className="context-sentence"><span className="quote-mark">“</span><p>{word.sentence}</p></div>
      <section className="meaning-section"><div className="section-label"><span>在这里，它的意思是</span>{explanation && <span className={`source-badge ${explanation.source}`}>{explanation.source === 'llm' ? 'AI 语境释义' : '离线基础释义'}</span>}</div>
        {!explanation && !loading && !error && <button className="button related-button" onClick={() => setRetry(v => v + 1)}><BookOpen size={16} />查看语境释义</button>}
        {loading && <div className="loading-copy" role="status"><LoaderCircle size={16} className="spin" />正在结合原文理解这个词…</div>}
        {error && <div className="inline-error" role="alert"><p>{error}</p><button className="text-button" onClick={() => setRetry(v => v + 1)}><RefreshCw size={13} />重新获取</button></div>}
        {explanation && <><h3>{explanation.meaning_in_context}</h3><p>{explanation.simple_explanation}</p>{typeof explanation.importance === 'number' && Number.isFinite(explanation.importance) && <p className="context-importance">文中重要程度 · <strong>{Math.round(explanation.importance * 100)} / 100</strong><span>{explanation.source === 'offline' ? '离线词表参考值，仅供阅读参考。' : '帮助安排阅读顺序，仅供参考。'}</span></p>}{explanation.warning && <p className="service-note">{explanation.warning}</p>}</>}
      </section>
      <SentencePanel word={word} disabled={saving} analysisCache={sentenceCache} pageStart={sentencePageStart} analysisKey={sentenceKey} />
      <WordHistoryPanel lemma={word.lemma} updatedAt={vocabulary.find(entry => entry.lemma === word.lemma)?.updated_at} revision={historyRevision} />
      {syncError && <div className="learning-sync-error inline-error" role="alert"><p>词库统计刷新失败：{syncError}</p><button className="text-button" onClick={() => void onLearningChanged().then(() => { setSyncError(''); setHistoryRevision(revision => revision + 1); }).catch(e => setSyncError(errorMessage(e)))}><RefreshCw size={13} />刷新词库统计</button></div>}
      <section className="related-section"><div className="section-label"><span><Sparkles size={14} />关联词推荐</span><span className="tiny-badge">≤ 3 个词</span></div>
        <p className="related-intro">结合这篇文章和你的掌握情况，只挑值得一起学的词。</p>
        {!recommendation && !recommending && <button className="button related-button" onClick={() => setRelatedRequest(v => v + 1)}><ArrowDownRight size={16} />发现关联词</button>}
        {recommending && <div className="loading-copy" role="status"><LoaderCircle size={16} className="spin" />正在筛选适合你的关联词…</div>}
        {recommendError && <div className="inline-error" role="alert"><p>{recommendError}</p><button className="text-button" onClick={() => setRelatedRequest(v => v + 1)}>重试</button></div>}
        {recommendation && <><div className="recommendation-meta"><span className={`source-badge ${recommendation.source}`}>{recommendation.source === 'llm' ? 'AI 个性化推荐' : '离线规则推荐'}</span><button className="text-button" disabled={recommending} onClick={() => setRelatedRequest(v => v + 1)} aria-label="刷新关联推荐"><RefreshCw size={12} /></button></div>
          {recommendation.items.slice(0, 3).map(item => <RelatedWordCard key={`${recommendation.batch_id}:${item.word}`} item={item} batchId={recommendation.batch_id} disabled={saving || recommending} status={vocabulary.find(entry => entry.lemma === item.word.toLowerCase() || entry.word.toLowerCase() === item.word.toLowerCase())?.status ?? null} onSaved={feedbackSaved} />)}
          {!recommendation.items.length && <p className="service-note">暂时没有合适的关联词。继续阅读，积累更多语境。</p>}
          {recommendation.items.length > 0 && <p className="service-note">学习优先级按语境和学习记录的规则评分，用于安排学习顺序。</p>}
          {recommendation.warning && <p className="service-note">{recommendation.warning}</p>}
        </>}
      </section>
    </div>
  </aside>;
}
