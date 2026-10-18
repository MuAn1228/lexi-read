import { Languages, LoaderCircle, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { errorMessage, request } from './api';
import { sentenceProblem } from './sentenceAnalysis';
import type { SentenceAnalysis } from './sentenceAnalysis';
import type { Occurrence } from './types';
import './sentencePanel.css';

export default function SentencePanel({ word, disabled }: { word: Occurrence; disabled: boolean }) {
  const [result, setResult] = useState<{ occurrenceId: number; value: SentenceAnalysis } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const pending = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const problem = sentenceProblem(word.sentence);
  const value = result?.occurrenceId === word.id ? result.value : null;
  useEffect(() => {
    ++generation.current; controller.current?.abort(); pending.current = false;
    setResult(null); setError(''); setLoading(false);
    return () => { ++generation.current; controller.current?.abort(); };
  }, [word.id, word.sentence]);
  const analyze = async () => {
    if (disabled || pending.current || problem) return;
    const current = ++generation.current;
    const abort = new AbortController(); controller.current = abort;
    pending.current = true; setLoading(true); setError('');
    try {
      const analysis = await request<SentenceAnalysis>('/sentences/analyze', { method: 'POST', body: JSON.stringify({ occurrence_id: word.id }), signal: abort.signal });
      if (current === generation.current && !abort.signal.aborted) setResult({ occurrenceId: word.id, value: analysis });
    } catch (e) { if (current === generation.current && !abort.signal.aborted) setError(errorMessage(e)); }
    finally { if (current === generation.current && !abort.signal.aborted) { pending.current = false; setLoading(false); controller.current = null; } }
  };
  return <section className="sentence-analysis" aria-label="整句翻译与长难句解析">
    <div className="section-label"><span><Languages size={14} />读懂这一整句</span>{value?.source === 'llm' && <span className="source-badge llm">AI 句子解析</span>}</div>
    <p className="sentence-analysis-intro">从句子结构和语法，理解当前单词所在的原句。</p>
    {problem ? <p className="service-note" role="status">{problem}</p> : <>
      {!value && !loading && !error && <button className="button related-button" disabled={disabled} onClick={() => void analyze()}><Languages size={16} />翻译与解析本句</button>}
      {loading && <div className="loading-copy" role="status"><LoaderCircle size={16} className="spin" />正在翻译并拆解这句话…</div>}
      {error && <div className="inline-error" role="alert"><p>{error}</p><button className="text-button" disabled={disabled || loading} onClick={() => void analyze()}><RefreshCw size={13} />重试句子解析</button></div>}
      {value?.source === 'unavailable' && <div className="sentence-unavailable" role="status"><p>{value.warning || '句子解析暂不可用，请检查模型服务后重试。'}</p><button className="text-button" disabled={disabled || loading} onClick={() => void analyze()}><RefreshCw size={13} />重试句子解析</button></div>}
      {value?.source === 'llm' && <div className="sentence-analysis-result" aria-label="本句解析结果">
        {value.cached && <p className="sentence-cache-note">已复用本句解析，无需重复生成。</p>}
        {value.translation && <div className="sentence-translation"><h3>中文翻译</h3><p>{value.translation}</p></div>}
        {value.structure && <div className="sentence-structure"><h3>句子结构</h3><p>{value.structure}</p></div>}
        {value.clauses.length > 0 && <div className="sentence-clauses"><h3>分句拆解</h3><ol>{value.clauses.map((clause, index) => <li key={index}><span className="clause-role">{clause.role}</span><p className="clause-original" lang="en">{clause.text}</p><p className="clause-explanation">{clause.explanation}</p></li>)}</ol></div>}
        {value.key_points.length > 0 && <div className="sentence-key-points"><h3>语法要点</h3><ul>{value.key_points.map((point, index) => <li key={index}>{point}</li>)}</ul></div>}
        {value.warning && <p className="service-note">{value.warning}</p>}
      </div>}
    </>}
  </section>;
}
