import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { BookOpen, Clock3, LoaderCircle, Plus, RefreshCw, Search, Sparkles, Square, Trash2 } from 'lucide-react';
import { errorMessage, request } from './api';
import { dictionaryAnnotationsPath, dictionaryGenerationLabel, dictionaryPath } from './dictionary';
import type { DictionaryResult } from './dictionary';
import { lookupQueryProblem, normalizeLookupQuery, pushLookupHistory } from './dictionaryLookup';
import PronunciationButton, { SpeechSettings } from './PronunciationButton';
import WordAnnotations from './WordAnnotations';
import { StatusBadge, WordControls } from './WordControls';
import { useMascot } from './mascot/useMascot';
import type { Vocabulary, WordStatus } from './types';
import './dictionaryLookup.css';

export default function DictionaryLookupView({ active, vocabulary, disabled, onSaved, onBusyChange, onGenerated }: {
  active: boolean; vocabulary: Vocabulary[]; disabled: boolean;
  onSaved: (item: Vocabulary) => void; onBusyChange: (busy: boolean) => void;
  onGenerated?: (result: DictionaryResult) => void;
}) {
  const mascot = useMascot();
  const inputId = useId();
  const helpId = useId();
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [result, setResult] = useState<DictionaryResult | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [readingAnnotations, setReadingAnnotations] = useState(false);
  const [saving, setSaving] = useState(false);
  const [inputError, setInputError] = useState('');
  const [error, setError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [notice, setNotice] = useState('');
  const [savedEntry, setSavedEntry] = useState<Vocabulary | null>(null);
  const [failedStatus, setFailedStatus] = useState<WordStatus | null>(null);
  const lookup = useRef<AbortController | null>(null);
  const generation = useRef<AbortController | null>(null);
  const annotations = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  const savingRef = useRef(false);
  const activeRef = useRef(active);
  const mounted = useRef(true);
  const resultRef = useRef(result);
  activeRef.current = active;

  const cancelReads = useCallback(() => {
    ++sequence.current;
    lookup.current?.abort(); lookup.current = null;
    generation.current?.abort(); generation.current = null;
    annotations.current?.abort(); annotations.current = null;
  }, []);

  useEffect(() => {
    if (active) return;
    cancelReads();
    setLoading(false); setGenerating(false); setReadingAnnotations(false);
  }, [active, cancelReads]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; cancelReads(); };
  }, [cancelReads]);

  const updateResult = (value: DictionaryResult | null) => {
    resultRef.current = value;
    setResult(value);
  };

  const search = async (text: string) => {
    if (!activeRef.current || disabled || savingRef.current) return;
    const problem = lookupQueryProblem(text);
    if (problem) { setInputError(problem); return; }
    const word = normalizeLookupQuery(text);
    cancelReads();
    const current = sequence.current;
    const controller = new AbortController(); lookup.current = controller;
    const isCurrent = () => activeRef.current && mounted.current && !controller.signal.aborted
      && sequence.current === current && lookup.current === controller;
    setQuery(word); setSubmitted(word); updateResult(null);
    setLoading(true); setGenerating(false); setReadingAnnotations(false);
    setInputError(''); setError(''); setSaveError(''); setNotice(''); setFailedStatus(null);
    try {
      const value = await request<DictionaryResult>(dictionaryPath(word), { signal: controller.signal });
      if (!isCurrent()) return;
      updateResult(value);
      setHistory(items => pushLookupHistory(items, word));
    } catch (reason) {
      if (isCurrent()) setError(errorMessage(reason));
    } finally {
      if (isCurrent()) { lookup.current = null; setLoading(false); }
    }
  };

  const stopGeneration = () => {
    generation.current?.abort(); generation.current = null;
    setGenerating(false); setNotice('已停止等待详细讲解，可随时重新生成。');
  };

  const generate = async () => {
    const entry = resultRef.current;
    if (!activeRef.current || disabled || savingRef.current || !entry?.can_generate || entry.detail
      || lookup.current || generation.current || annotations.current) return;
    const controller = new AbortController(); generation.current = controller;
    const current = sequence.current;
    const isCurrent = () => activeRef.current && mounted.current && !controller.signal.aborted
      && sequence.current === current && generation.current === controller;
    setGenerating(true); setError(''); setNotice('');
    try {
      const value = await request<DictionaryResult>(dictionaryPath(entry.word, true), { method: 'POST', signal: controller.signal });
      if (!isCurrent()) return;
      updateResult(value);
      if (value.detail) onGenerated?.(value);
    } catch (reason) {
      if (isCurrent()) setError(errorMessage(reason));
    } finally {
      if (isCurrent()) { generation.current = null; setGenerating(false); }
    }
  };

  const readAnnotations = async () => {
    const entry = resultRef.current;
    if (!activeRef.current || disabled || savingRef.current || !entry?.can_fetch_annotations
      || lookup.current || generation.current || annotations.current) return;
    const controller = new AbortController(); annotations.current = controller;
    const current = sequence.current;
    const isCurrent = () => activeRef.current && mounted.current && !controller.signal.aborted
      && sequence.current === current && annotations.current === controller;
    setReadingAnnotations(true); setError(''); setNotice('');
    try {
      const value = await request<DictionaryResult>(dictionaryAnnotationsPath(entry.word), {
        method: 'POST', body: JSON.stringify({ refresh: Boolean(entry.maimemo) }), signal: controller.signal,
      });
      if (isCurrent()) updateResult(value);
    } catch (reason) {
      if (isCurrent()) setError(errorMessage(reason));
    } finally {
      if (isCurrent()) { annotations.current = null; setReadingAnnotations(false); }
    }
  };

  const matchesEntry = (item: Vocabulary, entry: DictionaryResult) => item.lemma.toLowerCase() === entry.lemma.toLowerCase()
    || item.word.toLowerCase() === entry.word.toLowerCase();
  const storedEntry = result ? vocabulary.find(item => matchesEntry(item, result)) : undefined;
  const personalEntry = result && savedEntry && matchesEntry(savedEntry, result)
    && (!storedEntry || new Date(savedEntry.updated_at).getTime() >= new Date(storedEntry.updated_at).getTime())
    ? savedEntry : storedEntry;

  const saveStatus = async (status: WordStatus) => {
    const entry = resultRef.current;
    if (!activeRef.current || !entry || disabled || savingRef.current || lookup.current || personalEntry?.status === status) return;
    savingRef.current = true; setSaving(true); onBusyChange(true);
    setSaveError(''); setFailedStatus(null); setNotice('');
    try {
      const item = await request<Vocabulary>(`/vocabulary/${encodeURIComponent(entry.word)}`, {
        method: 'PUT', body: JSON.stringify({ status }),
      });
      onSaved(item);
      if (item.status) mascot.emit({ type: 'WORD_MARKED', word: item.word, status: item.status });
      if (mounted.current) {
        setSavedEntry(item);
        setNotice(personalEntry ? '掌握状态已保存。' : '已加入生词本。');
      }
    } catch (reason) {
      if (mounted.current) { setSaveError(errorMessage(reason)); setFailedStatus(status); }
    } finally {
      savingRef.current = false;
      if (mounted.current) setSaving(false);
      onBusyChange(false);
    }
  };

  if (!active) return null;
  const actionsDisabled = disabled || saving;
  return <div className="dictionary-lookup-view">
    <section className="panel lookup-search-panel" aria-label="独立查词">
      <div className="lookup-search-heading"><BookOpen size={22} aria-hidden="true" /><div><h2>输入英文单词或短语</h2><p>先查看本地释义，需要时再补充详细讲解。</p></div></div>
      <form className="lookup-form" onSubmit={event => { event.preventDefault(); void search(query); }}>
        <label htmlFor={inputId}>英文单词或短语</label>
        <div className="lookup-input-row"><div className="lookup-input-wrap"><Search size={19} aria-hidden="true" /><input id={inputId} aria-label="查询英文单词或短语" value={query} placeholder="例如 resilient 或 take into account" autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={240} disabled={actionsDisabled} aria-describedby={helpId} aria-invalid={Boolean(inputError)} onChange={event => { setQuery(event.target.value); setInputError(''); }} /></div><button type="submit" className="button primary" disabled={actionsDisabled || !query.trim()}><Search size={17} />查单词</button></div>
        <p id={helpId} className={inputError ? 'lookup-input-error' : 'lookup-search-hint'} role={inputError ? 'alert' : undefined}>{inputError || '按回车查询 · 查询本身不会加入生词本或改变复习安排'}</p>
      </form>
    </section>

    <div className="lookup-columns">
      <section className="panel lookup-result-panel lookup-result" aria-label="查词结果" aria-busy={loading}>
        {loading && <div className="lookup-loading" role="status"><LoaderCircle size={22} className="spin" /><p>正在查询 <span lang="en">{submitted}</span> 的本地释义…</p><span>可以继续输入并查询其他单词。</span></div>}
        {!loading && error && !result && <div className="lookup-query-error" role="alert"><p>{error}</p><button type="button" className="button secondary" disabled={actionsDisabled} onClick={() => void search(submitted)}><RefreshCw size={15} />重新查询</button></div>}
        {!loading && !result && !error && <div className="lookup-empty"><Search size={27} aria-hidden="true" /><h3>从一个单词开始</h3><p>输入英文单词或短语，即可查看释义。</p></div>}
        {result && <>
          <header className="lookup-word-heading"><div><div className="lookup-word-title"><h2 lang="en">{result.word}</h2>{personalEntry && <StatusBadge status={personalEntry.status} />}</div>{result.dictionary?.phonetic && <p className="dictionary-phonetic">/{result.dictionary.phonetic.replace(/^\/+|\/+$/g, '')}/</p>}{result.dictionary && result.dictionary.headword.toLowerCase() !== result.word.toLowerCase() && <p className="dictionary-headword">词典词形 · <span lang="en">{result.dictionary.headword}</span></p>}</div><PronunciationButton key={result.word} text={result.word} label="朗读单词" /></header>
          <details className="lookup-speech-settings"><summary>发音设置</summary><SpeechSettings /></details>
          <div className="lookup-vocabulary-actions">
            {personalEntry ? <><span className="lookup-status-caption">已在生词本 · 调整掌握状态</span><div className="lookup-word-controls"><WordControls word={result.word} status={personalEntry.status} disabled={actionsDisabled} compact showLabels disableCurrent onMark={status => void saveStatus(status)} /></div></> : <><p>想在之后复习这个词？</p><button type="button" className="button secondary" disabled={actionsDisabled} onClick={() => void saveStatus('UNKNOWN')}>{saving ? <LoaderCircle size={16} className="spin" /> : <Plus size={16} />}{saving ? '正在加入…' : '加入生词本'}</button></>}
            {saving && personalEntry && <span className="lookup-saving" role="status"><LoaderCircle size={14} className="spin" />正在保存…</span>}
          </div>
          {saveError && <div className="dictionary-error" role="alert"><p>保存失败：{saveError}</p>{failedStatus && <button type="button" className="text-button" disabled={actionsDisabled} onClick={() => void saveStatus(failedStatus)}>重试保存</button>}</div>}
          {error && <p className="dictionary-error" role="alert">{error}</p>}
          {notice && <p className="lookup-notice" role="status">{notice}</p>}
          <WordAnnotations result={result}
            annotationAction={result.can_fetch_annotations ? <div className="dictionary-annotation-actions"><button type="button" className="text-button" disabled={actionsDisabled || readingAnnotations || generating} onClick={() => void readAnnotations()}>{readingAnnotations ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}{readingAnnotations ? '正在读取墨墨个人注解…' : result.maimemo ? '更新墨墨个人注解' : '读取墨墨个人注解'}</button></div> : undefined}
            detailAction={<div className="dictionary-generate"><p>补充常见义项、双语例句和搭配。点击后调用 AI，生成的讲解会保存在本地。</p><div className="lookup-generate-actions"><button type="button" className="button primary" disabled={actionsDisabled || generating || readingAnnotations || !result.can_generate} onClick={() => void generate()}>{generating ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={16} />}{generating ? '正在生成详细讲解…' : dictionaryGenerationLabel(result)}</button>{generating && <button type="button" className="text-button" onClick={stopGeneration}><Square size={13} />停止等待</button>}</div>{generating && <p role="status">正在整理义项和例句，可以继续查询其他词。</p>}{!result.can_generate && <p className="dictionary-empty">请先完成本机 AI 服务配置；本地释义仍可查看。</p>}</div>}
          />
        </>}
      </section>
      <aside className="panel lookup-history-panel lookup-history" aria-label="最近查询">
        <header><h2><Clock3 size={16} aria-hidden="true" />最近查询</h2>{history.length > 0 && <button type="button" className="text-button" disabled={actionsDisabled} onClick={() => setHistory([])} aria-label="清空最近查询"><Trash2 size={13} />清空</button>}</header>
        <p className="lookup-history-note">仅保留本次打开的最近 12 条查询。</p>
        {history.length ? <ol>{history.map(word => <li key={word.toLocaleLowerCase()}><button type="button" disabled={actionsDisabled} aria-current={result?.word.toLocaleLowerCase() === word.toLocaleLowerCase() ? 'true' : undefined} onClick={() => void search(word)}><span lang="en">{word}</span><Search size={13} aria-hidden="true" /></button></li>)}</ol> : <p className="lookup-history-empty">查询过的单词会显示在这里。</p>}
      </aside>
    </div>
  </div>;
}
