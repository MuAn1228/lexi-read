import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { BookOpen, Check, LoaderCircle, RefreshCw, Sparkles, X } from 'lucide-react';
import { errorMessage, request } from './api';
import { dictionaryGenerationLabel, dictionaryPath } from './dictionary';
import type { DictionaryResult } from './dictionary';
import { ThoughtLine } from './components/StudyInteractions';
import PronunciationButton, { SpeechSettings } from './PronunciationButton';
import WordExpansion from './WordExpansionPanel';
import type { Vocabulary } from './types';
import type { WordExpansionCache } from './wordExpansion';
import './dictionary.css';

type OpenDictionary = (word: string, opener: HTMLElement) => void;
const DictionaryContext = createContext<OpenDictionary>(() => {});
export function useDictionary() { return useContext(DictionaryContext); }

/** One shared dialog leaves the reading and card sessions mounted underneath. */
export function DictionaryProvider({ children, onGenerated, vocabulary, onLearningChanged, resetKey = false }: {
  children: ReactNode; onGenerated?: (result: DictionaryResult) => void;
  vocabulary?: Vocabulary[]; onLearningChanged?: () => Promise<void>; resetKey?: boolean;
}) {
  const [target, setTarget] = useState<{ word: string; opener: HTMLElement; sequence: number } | null>(null);
  const sequence = useRef(0);
  const expansionCache = useRef<WordExpansionCache>(new Map());
  // A restored database may reuse batch IDs; old dialogs and receipts must not survive it.
  useEffect(() => { expansionCache.current.clear(); setTarget(null); }, [resetKey]);
  const open = useCallback<OpenDictionary>((word, opener) => {
    if (word.trim()) setTarget({ word: word.trim(), opener, sequence: ++sequence.current });
  }, []);
  const close = useCallback(() => setTarget(null), []);
  return <DictionaryContext.Provider value={open}>{children}{target && <DictionaryPanel key={target.sequence} word={target.word} opener={target.opener} onClose={close} onGenerated={onGenerated} expansionCache={expansionCache.current} vocabulary={vocabulary} onLearningChanged={onLearningChanged} />}</DictionaryContext.Provider>;
}

function DictionaryPanel({ word, opener, onClose, onGenerated, expansionCache, vocabulary, onLearningChanged }: {
  word: string; opener: HTMLElement; onClose: () => void; onGenerated?: (result: DictionaryResult) => void;
  expansionCache: WordExpansionCache; vocabulary?: Vocabulary[]; onLearningChanged?: () => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const titleId = useId(); const descriptionId = useId();
  const [result, setResult] = useState<DictionaryResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const generation = useRef<AbortController | null>(null);

  useEffect(() => {
    const modal = dialog.current;
    if (!modal) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Native modal dialogs enter the browser's top layer and isolate nested dialogs.
    modal.showModal(); closeButton.current?.focus();
    return () => {
      generation.current?.abort(); generation.current = null;
      modal.close(); document.body.style.overflow = overflow;
      if (opener.isConnected && !opener.matches(':disabled')) opener.focus();
    };
  }, [opener]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setResult(null);
    request<DictionaryResult>(dictionaryPath(word), { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setResult(value); })
      .catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [word, retry]);

  const generate = async () => {
    if (!result?.can_generate || result.detail || generation.current) return;
    const controller = new AbortController(); generation.current = controller;
    setGenerating(true); setError('');
    try {
      const value = await request<DictionaryResult>(dictionaryPath(word, true), { method: 'POST', signal: controller.signal });
      if (!controller.signal.aborted) { setResult(value); if (value.detail) onGenerated?.(value); }
    } catch (reason) { if (!controller.signal.aborted) setError(errorMessage(reason)); }
    finally {
      if (!controller.signal.aborted) { setGenerating(false); generation.current = null; }
    }
  };
  const entry = result?.dictionary;
  const detail = result?.detail;

  return createPortal(<dialog ref={dialog} className="dictionary-dialog" data-dictionary-dialog="" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1}
    onKeyDown={event => {
      if (event.key !== 'Tab') return;
      const modal = event.currentTarget;
      const elements = Array.from(modal.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], summary, [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
      const first = elements[0], last = elements[elements.length - 1];
      if (!first) { event.preventDefault(); modal.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === modal)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === modal)) { event.preventDefault(); first.focus(); }
    }}
    onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => {
      if (event.target !== event.currentTarget) return;
      const rect = event.currentTarget.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
    }}>
    <header className="dictionary-heading"><div><p className="dictionary-eyebrow"><BookOpen size={13} /> YOUR WORD COMPANION</p><h2 id={titleId}>{word}</h2>{entry?.phonetic && <p className="dictionary-phonetic">/{entry.phonetic.replace(/^\/+|\/+$/g, '')}/</p>}{entry && entry.headword.toLowerCase() !== word.toLowerCase() && <p className="dictionary-headword">词典词形 · {entry.headword}</p>}</div><button className="icon-button" type="button" ref={closeButton} aria-label="关闭中文释义" onClick={onClose}><X size={20} /></button></header>
    <p id={descriptionId} className="dictionary-intro">先读中文释义，再从义项、例句和搭配中理解用法。这里展示通用词义，文章中的具体含义可在“语境学习”中查看。</p>
    <div className="dictionary-speech"><PronunciationButton text={word} label="朗读单词" /><SpeechSettings /></div>
    <div className="dictionary-body" aria-busy={loading}>
      {loading && <p className="dictionary-loading" role="status"><LoaderCircle size={18} className="spin" />正在读取本地释义…</p>}
      {error && <div className="dictionary-error" role="alert"><p>{error}</p>{!result && <button type="button" className="text-button" onClick={() => setRetry(value => value + 1)}><RefreshCw size={14} />重新读取释义</button>}</div>}
      {result && <>
        <section className="dictionary-section dictionary-offline"><div className="dictionary-section-heading"><h3>中文释义</h3><span className="dictionary-badge">离线词典 · ECDICT</span></div>
          {entry ? <><ol className="dictionary-definitions">{entry.chinese.map((meaning, index) => <li key={index}>{meaning}</li>)}</ol>{entry.forms.length > 0 && <div className="dictionary-forms">{entry.forms.map((form, index) => <span key={index}><small>{form.label}</small>{form.word}</span>)}</div>}{entry.english.length > 0 && <details className="dictionary-english"><summary>展开英文释义（{entry.english.length}）</summary><ol>{entry.english.map((meaning, index) => <li key={index}>{meaning}</li>)}</ol></details>}<a className="dictionary-source-link" href="https://github.com/skywind3000/ECDICT" target="_blank" rel="noreferrer">词典来源与说明 ↗</a></> : <p className="dictionary-empty">离线词典暂未收录这个词。可以尝试生成 AI 详细讲解。</p>}
        </section>
        <WordExpansion word={word} cache={expansionCache} vocabulary={vocabulary} onLearningChanged={onLearningChanged} />
        <section className="dictionary-section dictionary-ai"><div className="dictionary-section-heading"><h3><Sparkles size={17} />详细讲解</h3>{detail && <span className="dictionary-badge dictionary-badge--ai">AI 生成{result.cached ? ' · 已缓存' : ''}</span>}</div>
          {!detail && <div className="dictionary-generate"><p>补充常见义项、词性、双语例句、常用搭配和易混辨析。点击后调用 AI，成功后保存在本地，方便下次查看。</p><button type="button" className="button primary" disabled={generating || !result.can_generate} onClick={() => void generate()}>{generating ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={16} />}{generating ? '正在生成详细讲解…' : dictionaryGenerationLabel(result)}</button>{generating && <ThoughtLine label="正在整理义项和双语例句，关闭窗口可停止等待。" />}{!result.can_generate && <p className="dictionary-empty">请先完成本机 AI 服务配置；离线词典仍可正常查看。</p>}</div>}
          {result.warning && <p className="dictionary-warning" role="status">{result.warning}</p>}
          {detail && <><p className="dictionary-summary">{detail.summary}</p><div className="dictionary-senses">{detail.senses.map((sense, index) => <article className="dictionary-sense" key={index}><div className="dictionary-sense-heading"><span className="dictionary-sense-number">{String(index + 1).padStart(2, '0')}</span><div><span className="dictionary-pos">{sense.part_of_speech}</span><h4>{sense.meaning}</h4></div></div><p className="dictionary-explanation">{sense.explanation}</p>{sense.examples.map((example, exampleIndex) => <blockquote className="dictionary-example" key={exampleIndex}><p lang="en">{example.english}</p><p>{example.chinese}</p></blockquote>)}</article>)}</div>
            {detail.collocations.length > 0 && <div className="dictionary-detail-group"><h4>常用搭配</h4><div className="dictionary-collocations">{detail.collocations.map((item, index) => <article key={index}><strong lang="en">{item.phrase}</strong><span>{item.meaning}</span><p lang="en">{item.example}</p><p>{item.translation}</p></article>)}</div></div>}
            {detail.usage_notes.length > 0 && <div className="dictionary-detail-group"><h4>用法提示</h4><ul className="dictionary-usage">{detail.usage_notes.map((note, index) => <li key={index}><Check size={14} /><span>{note}</span></li>)}</ul></div>}
            {detail.confusables.length > 0 && <div className="dictionary-detail-group"><h4>易混辨析</h4><dl className="dictionary-confusables">{detail.confusables.map((item, index) => <div key={index}><dt lang="en">{item.word}</dt><dd>{item.distinction}</dd></div>)}</dl></div>}
            <p className="dictionary-ai-note">例句与讲解由 AI 生成，具体用法请结合原文判断。</p>
          </>}
        </section>
      </>}
    </div>
    <footer className="dictionary-footer">查看词典不会改变掌握状态或复习安排。<button type="button" className="text-button" onClick={onClose}>返回学习</button></footer>
  </dialog>, document.body);
}
