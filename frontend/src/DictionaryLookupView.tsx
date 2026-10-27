import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { BookOpen, Clock3, LoaderCircle, Plus, RefreshCw, Search, Sparkles, Trash2 } from 'lucide-react';
import { errorMessage, learningStorageKey, request } from './api';
import { requestAIStream } from './aiStream';
import type { AIStreamPhase } from './aiStream';
import AIWaitPanel from './components/AIWaitPanel';
import { dictionaryAnnotationsPath, dictionaryGenerationLabel, dictionaryPath } from './dictionary';
import type { DictionaryResult } from './dictionary';
import { lookupQueryProblem, normalizeLookupQuery, pushLookupHistory, readLookupHistory, writeLookupHistory } from './dictionaryLookup';
import PronunciationButton, { SpeechSettings } from './PronunciationButton';
import WordAnnotations from './WordAnnotations';
import { StatusBadge, WordControls } from './WordControls';
import { useMascot } from './mascot/useMascot';
import type { Vocabulary, WordStatus } from './types';
import './dictionaryLookup.css';

interface LookupSuggestion { word: string; meaning: string }
interface LookupSuggestions {
  query: string; mode: 'prefix' | 'spelling'; items: LookupSuggestion[]; warning: string | null;
}

function usableSuggestions(value: LookupSuggestions): LookupSuggestion[] {
  return Array.isArray(value.items) ? value.items.filter(item => item && typeof item.word === 'string'
    && typeof item.meaning === 'string' && !lookupQueryProblem(item.word)).slice(0, 8) : [];
}

function dictionaryUnavailable(warning: string | null): boolean {
  return Boolean(warning && /词典[^。；\n]*(?:不可用|故障|失败|损坏)/.test(warning));
}

export default function DictionaryLookupView({ active, vocabulary, disabled, onSaved, onBusyChange, onGenerated, onLearningChanged }: {
  active: boolean; vocabulary: Vocabulary[]; disabled: boolean;
  onSaved: (item: Vocabulary) => void; onBusyChange: (busy: boolean) => void;
  onGenerated?: (result: DictionaryResult) => void;
  onLearningChanged?: () => void | Promise<void>;
}) {
  const mascot = useMascot();
  const inputId = useId();
  const helpId = useId();
  const suggestionsId = useId();
  const [query, setQuery] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [result, setResult] = useState<DictionaryResult | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [historyUnavailable, setHistoryUnavailable] = useState(false);
  const [inputFocused, setInputFocused] = useState(false);
  const [inputEdited, setInputEdited] = useState(false);
  const [composing, setComposing] = useState(false);
  const [prefixItems, setPrefixItems] = useState<LookupSuggestion[]>([]);
  const [prefixOpen, setPrefixOpen] = useState(false);
  const [prefixWarning, setPrefixWarning] = useState(false);
  const [selectedSuggestion, setSelectedSuggestion] = useState(-1);
  const [spellingItems, setSpellingItems] = useState<LookupSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generationPhase, setGenerationPhase] = useState<AIStreamPhase>('connecting');
  const [generationPreview, setGenerationPreview] = useState('');
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
  const prefix = useRef<AbortController | null>(null);
  const prefixTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prefixSequence = useRef(0);
  const spelling = useRef<AbortController | null>(null);
  const spellingSequence = useRef(0);
  const suggestionsList = useRef<HTMLUListElement | null>(null);
  const composingRef = useRef(false);
  const focusedRef = useRef(false);
  const historyRef = useRef<string[]>([]);
  const historyKey = useRef<string | null>(null);
  const historyKeyPromise = useRef<Promise<string> | null>(null);
  const historyHydrated = useRef(false);
  const historyLoading = useRef(false);
  const historyDirty = useRef(false);
  const historyClearVersion = useRef(0);
  const sequence = useRef(0);
  const savingRef = useRef(false);
  const activeRef = useRef(active);
  const mounted = useRef(true);
  const resultRef = useRef(result);
  const queryRef = useRef(query);
  activeRef.current = active;
  queryRef.current = query;

  const cancelPrefix = useCallback(() => {
    ++prefixSequence.current;
    if (prefixTimer.current !== null) clearTimeout(prefixTimer.current);
    prefixTimer.current = null;
    prefix.current?.abort(); prefix.current = null;
  }, []);

  const closePrefix = useCallback(() => {
    cancelPrefix();
    setPrefixItems([]); setPrefixOpen(false); setPrefixWarning(false); setSelectedSuggestion(-1);
  }, [cancelPrefix]);

  const cancelSpelling = useCallback(() => {
    ++spellingSequence.current;
    spelling.current?.abort(); spelling.current = null;
  }, []);

  const cancelReads = useCallback(() => {
    ++sequence.current;
    lookup.current?.abort(); lookup.current = null;
    generation.current?.abort(); generation.current = null;
    annotations.current?.abort(); annotations.current = null;
    cancelPrefix(); cancelSpelling();
  }, [cancelPrefix, cancelSpelling]);

  const persistHistory = useCallback((items: string[]) => {
    if (!historyHydrated.current || !historyKey.current) return;
    setHistoryUnavailable(!writeLookupHistory(historyKey.current, items));
  }, []);

  const replaceHistory = useCallback((items: string[]) => {
    historyRef.current = items;
    historyDirty.current = true;
    setHistory(items);
    persistHistory(items);
  }, [persistHistory]);

  useEffect(() => {
    if (!active || historyHydrated.current || historyLoading.current) return;
    historyLoading.current = true;
    // Resolving the scope may create a guest session, so wait until the view first opens.
    historyKeyPromise.current ??= learningStorageKey('dictionary-history:v1');
    void (async () => {
      try {
        const key = await historyKeyPromise.current!;
        if (!mounted.current) return;
        historyKey.current = key;
        if (!activeRef.current) return;
        const stored = readLookupHistory(key);
        let items = historyClearVersion.current === 0 ? stored.history : [];
        for (const word of [...historyRef.current].reverse()) items = pushLookupHistory(items, word);
        historyHydrated.current = true;
        historyRef.current = items;
        setHistory(items);
        setHistoryUnavailable(!stored.available);
        if (historyDirty.current) persistHistory(items);
      } catch {
        if (mounted.current) {
          historyHydrated.current = true;
          setHistoryUnavailable(true);
        }
      } finally {
        historyLoading.current = false;
      }
    })();
  }, [active, persistHistory]);

  useEffect(() => {
    const word = normalizeLookupQuery(query);
    if (!active || !inputFocused || !inputEdited || composing || disabled || saving
      || word.length < 2 || lookupQueryProblem(word)) {
      closePrefix();
      return;
    }
    cancelPrefix();
    const current = prefixSequence.current;
    prefixTimer.current = setTimeout(() => {
      prefixTimer.current = null;
      const controller = new AbortController(); prefix.current = controller;
      const isCurrent = () => activeRef.current && mounted.current && focusedRef.current && !composingRef.current
        && !controller.signal.aborted && prefixSequence.current === current && prefix.current === controller;
      void (async () => {
        try {
          const value = await request<LookupSuggestions>(`/dictionary/suggestions/query?q=${encodeURIComponent(word)}&mode=prefix`, { signal: controller.signal });
          if (!isCurrent() || value.mode !== 'prefix'
            || normalizeLookupQuery(value.query).toLowerCase() !== word.toLowerCase()) return;
          const items = usableSuggestions(value);
          setPrefixItems(items); setPrefixOpen(items.length > 0); setSelectedSuggestion(-1);
          setPrefixWarning(Boolean(value.warning));
        } catch {
          if (isCurrent()) setPrefixWarning(true);
        } finally {
          if (isCurrent()) prefix.current = null;
        }
      })();
    }, 200);
    return cancelPrefix;
  }, [active, query, inputFocused, inputEdited, composing, disabled, saving, cancelPrefix, closePrefix]);

  useEffect(() => {
    if (active) return;
    cancelReads();
    setLoading(false); setGenerating(false); setReadingAnnotations(false);
    setGenerationPreview('');
    focusedRef.current = false; composingRef.current = false;
    setInputFocused(false); setInputEdited(false); setComposing(false);
    setPrefixItems([]); setPrefixOpen(false); setPrefixWarning(false); setSelectedSuggestion(-1); setSpellingItems([]);
  }, [active, cancelReads]);

  useEffect(() => {
    if (prefixOpen && selectedSuggestion >= 0) {
      suggestionsList.current?.children[selectedSuggestion]?.scrollIntoView({ block: 'nearest' });
    }
  }, [prefixOpen, selectedSuggestion]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; cancelReads(); };
  }, [cancelReads]);

  const updateResult = (value: DictionaryResult | null) => {
    resultRef.current = value;
    setResult(value);
  };

  const loadSpelling = async (entry: DictionaryResult, lookupSequence: number) => {
    if (entry.dictionary || entry.detail || dictionaryUnavailable(entry.warning)) return;
    const word = normalizeLookupQuery(entry.word);
    if (normalizeLookupQuery(queryRef.current).toLowerCase() !== word.toLowerCase()) return;
    const current = ++spellingSequence.current;
    const controller = new AbortController(); spelling.current = controller;
    const isCurrent = () => activeRef.current && mounted.current && !controller.signal.aborted
      && sequence.current === lookupSequence && spellingSequence.current === current && spelling.current === controller
      && resultRef.current?.word === entry.word && !resultRef.current.dictionary && !resultRef.current.detail
      && normalizeLookupQuery(queryRef.current).toLowerCase() === word.toLowerCase();
    try {
      const value = await request<LookupSuggestions>(`/dictionary/suggestions/query?q=${encodeURIComponent(word)}&mode=spelling`, { signal: controller.signal });
      if (!isCurrent() || value.mode !== 'spelling'
        || normalizeLookupQuery(value.query).toLowerCase() !== word.toLowerCase()) return;
      setSpellingItems(usableSuggestions(value).filter(item => item.word.toLowerCase() !== word.toLowerCase()));
    } catch {
      // Optional spelling help must not replace the successful dictionary response.
    } finally {
      if (spelling.current === controller) spelling.current = null;
    }
  };

  const search = async (text: string) => {
    if (!activeRef.current || disabled || savingRef.current || composingRef.current) return;
    const problem = lookupQueryProblem(text);
    if (problem) { setInputError(problem); return; }
    const word = normalizeLookupQuery(text);
    cancelReads();
    closePrefix(); setInputEdited(false); setSpellingItems([]);
    const current = sequence.current;
    const controller = new AbortController(); lookup.current = controller;
    const isCurrent = () => activeRef.current && mounted.current && !controller.signal.aborted
      && sequence.current === current && lookup.current === controller;
    queryRef.current = word;
    setQuery(word); setSubmitted(word); updateResult(null);
    setLoading(true); setGenerating(false); setReadingAnnotations(false);
    setGenerationPreview('');
    setInputError(''); setError(''); setSaveError(''); setNotice(''); setFailedStatus(null);
    try {
      const value = await request<DictionaryResult>(dictionaryPath(word), { signal: controller.signal });
      if (!isCurrent()) return;
      updateResult(value);
      replaceHistory(pushLookupHistory(historyRef.current, word));
      void loadSpelling(value, current);
    } catch (reason) {
      if (isCurrent()) setError(errorMessage(reason));
    } finally {
      if (isCurrent()) { lookup.current = null; setLoading(false); }
    }
  };

  const stopGeneration = () => {
    generation.current?.abort(); generation.current = null;
    setGenerating(false); setGenerationPreview(''); setNotice('已停止等待详细讲解，可随时重新生成。');
  };

  const generate = async () => {
    const entry = resultRef.current;
    if (!activeRef.current || disabled || savingRef.current || !entry?.can_generate || entry.detail
      || lookup.current || generation.current || annotations.current) return;
    const controller = new AbortController(); generation.current = controller;
    const current = sequence.current;
    const isCurrent = () => activeRef.current && mounted.current && !controller.signal.aborted
      && sequence.current === current && generation.current === controller;
    setGenerating(true); setGenerationPhase('connecting'); setGenerationPreview(''); setError(''); setNotice('');
    try {
      const value = await requestAIStream<DictionaryResult>(`${dictionaryPath(entry.word)}/explain-stream`, { method: 'POST', signal: controller.signal }, {
        onStatus: phase => { if (isCurrent()) setGenerationPhase(phase); },
        onPreview: text => { if (isCurrent()) setGenerationPreview(text); },
      });
      if (!isCurrent()) return;
      updateResult(value);
      if (value.detail) onGenerated?.(value);
      else if (value.warning) setError(`${value.warning} 可重试生成，本地释义仍可查看。`);
    } catch (reason) {
      if (isCurrent()) setError(errorMessage(reason));
    } finally {
      if (isCurrent()) { generation.current = null; setGenerating(false); setGenerationPreview(''); }
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
      <form className="lookup-form" onSubmit={event => { event.preventDefault(); if (!composingRef.current) void search(query); }}>
        <label htmlFor={inputId}>英文单词或短语</label>
        <div className="lookup-input-row"><div className="lookup-input-wrap"><Search size={19} aria-hidden="true" /><input
          id={inputId} aria-label="查询英文单词或短语" role="combobox" aria-autocomplete="list" aria-haspopup="listbox"
          aria-expanded={prefixOpen && prefixItems.length > 0} aria-controls={prefixOpen ? suggestionsId : undefined}
          aria-activedescendant={prefixOpen && selectedSuggestion >= 0 ? `${suggestionsId}-${selectedSuggestion}` : undefined}
          value={query} placeholder="例如 resilient 或 take into account" autoComplete="off" autoCapitalize="none"
          spellCheck={false} maxLength={240} disabled={actionsDisabled} aria-describedby={helpId} aria-invalid={Boolean(inputError)}
          onFocus={() => { focusedRef.current = true; setInputFocused(true); }}
          onBlur={() => { focusedRef.current = false; setInputFocused(false); setInputEdited(false); closePrefix(); }}
          onChange={event => {
            queryRef.current = event.target.value;
            setQuery(event.target.value); setInputError(''); setInputEdited(true);
            closePrefix(); cancelSpelling(); setSpellingItems([]);
          }}
          onCompositionStart={() => { composingRef.current = true; setComposing(true); closePrefix(); }}
          onCompositionEnd={event => {
            composingRef.current = false; setComposing(false); setInputEdited(true);
            queryRef.current = event.currentTarget.value; setQuery(event.currentTarget.value);
          }}
          onKeyDown={event => {
            if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) {
              if (event.key === 'Enter') event.preventDefault();
              return;
            }
            if (event.key === 'Escape') {
              event.preventDefault(); event.stopPropagation(); setInputEdited(false); closePrefix(); return;
            }
            if (prefixOpen && prefixItems.length > 0 && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
              event.preventDefault();
              setSelectedSuggestion(index => event.key === 'ArrowDown'
                ? (index + 1) % prefixItems.length : index <= 0 ? prefixItems.length - 1 : index - 1);
            } else if (event.key === 'Enter' && prefixOpen && selectedSuggestion >= 0) {
              event.preventDefault(); void search(prefixItems[selectedSuggestion].word);
            }
          }}
        /></div><button type="submit" className="button primary" disabled={actionsDisabled || !query.trim()}><Search size={17} />查单词</button></div>
        {prefixOpen && prefixItems.length > 0 && <ul id={suggestionsId} ref={suggestionsList} className="lookup-suggestions" role="listbox" aria-label="输入候选">
          {prefixItems.map((item, index) => <li id={`${suggestionsId}-${index}`} key={item.word.toLowerCase()}
            role="option" aria-selected={selectedSuggestion === index} className={selectedSuggestion === index ? 'is-selected' : undefined}
            onPointerDown={event => event.preventDefault()} onPointerMove={() => setSelectedSuggestion(index)}
            onClick={() => void search(item.word)}><span lang="en">{item.word}</span><span>{item.meaning || '查看释义'}</span></li>)}
        </ul>}
        {prefixWarning && <p className="lookup-suggestion-warning" role="status">输入候选暂不可用，可直接查询。</p>}
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
            {personalEntry ? <><span className="lookup-status-caption">整词判断 · 调整整个单词的掌握状态</span><div className="lookup-word-controls"><WordControls word={result.word} status={personalEntry.status} disabled={actionsDisabled} compact showLabels disableCurrent onMark={status => void saveStatus(status)} /></div></> : <><p>想把整个单词加入之后的复习？</p><button type="button" className="button secondary" disabled={actionsDisabled} onClick={() => void saveStatus('UNKNOWN')}>{saving ? <LoaderCircle size={16} className="spin" /> : <Plus size={16} />}{saving ? '正在加入…' : '按整词加入生词本'}</button></>}
            {saving && personalEntry && <span className="lookup-saving" role="status"><LoaderCircle size={14} className="spin" />正在保存…</span>}
          </div>
          {saveError && <div className="dictionary-error" role="alert"><p>保存失败：{saveError}</p>{failedStatus && <button type="button" className="text-button" disabled={actionsDisabled} onClick={() => void saveStatus(failedStatus)}>重试保存</button>}</div>}
          {error && <p className="dictionary-error" role="alert">{error}</p>}
          {notice && <p className="lookup-notice" role="status">{notice}</p>}
          {!result.dictionary && !result.detail && spellingItems.length > 0 && <section className="lookup-spelling" aria-label="拼写建议">
            <p>你是不是想查</p>
            <div>{spellingItems.map(item => <button type="button" key={item.word.toLowerCase()} disabled={actionsDisabled}
              onClick={() => void search(item.word)}><span lang="en">{item.word}</span><span>{item.meaning || '查看释义'}</span></button>)}</div>
          </section>}
          <WordAnnotations result={result} enableSenseLearning senseLearningDisabled={actionsDisabled} onSenseLearningChanged={onLearningChanged}
            annotationAction={result.can_fetch_annotations ? <div className="dictionary-annotation-actions"><button type="button" className="text-button" disabled={actionsDisabled || readingAnnotations || generating} onClick={() => void readAnnotations()}>{readingAnnotations ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}{readingAnnotations ? '正在读取墨墨个人注解…' : result.maimemo ? '更新墨墨个人注解' : '读取墨墨个人注解'}</button></div> : undefined}
            detailAction={<div className="dictionary-generate"><p>补充常见义项、双语例句和搭配。点击后调用 AI，生成的讲解会保存在本地。</p>{generating ? <AIWaitPanel label="正在补充详细讲解" phase={generationPhase} preview={generationPreview} onStop={stopGeneration} /> : <div className="lookup-generate-actions"><button type="button" className="button primary" disabled={actionsDisabled || readingAnnotations || !result.can_generate} onClick={() => void generate()}><Sparkles size={16} />{dictionaryGenerationLabel(result)}</button></div>}{!result.can_generate && <p className="dictionary-empty">请先完成本机 AI 服务配置；本地释义仍可查看。</p>}</div>}
          />
        </>}
      </section>
      <aside className="panel lookup-history-panel lookup-history" aria-label="最近查询">
        <header><h2><Clock3 size={16} aria-hidden="true" />最近查询</h2>{history.length > 0 && <button type="button" className="text-button" disabled={actionsDisabled} onClick={() => { ++historyClearVersion.current; replaceHistory([]); }} aria-label="清空最近查询"><Trash2 size={13} />清空</button>}</header>
        <p className="lookup-history-note">保留最近 12 条查询，刷新后仍可查看。</p>
        {historyUnavailable && <p className="lookup-history-warning" role="status">本次查询仍可使用，刷新后可能无法恢复历史</p>}
        {history.length ? <ol>{history.map(word => <li key={word.toLocaleLowerCase()}><button type="button" disabled={actionsDisabled} aria-current={result?.word.toLocaleLowerCase() === word.toLocaleLowerCase() ? 'true' : undefined} onClick={() => void search(word)}><span lang="en">{word}</span><Search size={13} aria-hidden="true" /></button></li>)}</ol> : <p className="lookup-history-empty">查询过的单词会显示在这里。</p>}
      </aside>
    </div>
  </div>;
}
