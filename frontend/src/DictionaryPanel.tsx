import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { BookOpen, LoaderCircle, MessageCircle, RefreshCw, Sparkles, X } from 'lucide-react';
import { useMascot, useMascotSnapshot } from './mascot/useMascot';
import { errorMessage, request } from './api';
import { dictionaryAnnotationsPath, dictionaryGenerationLabel, dictionaryPath } from './dictionary';
import type { DictionaryResult } from './dictionary';
import { ThoughtLine } from './components/StudyInteractions';
import PronunciationButton, { SpeechSettings } from './PronunciationButton';
import WordExpansion from './WordExpansionPanel';
import WordAnnotations from './WordAnnotations';
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
  const mascot = useMascot();
  const { suppressed: assistantSuppressed } = useMascotSnapshot();
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const titleId = useId(); const descriptionId = useId();
  const [result, setResult] = useState<DictionaryResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [readingAnnotations, setReadingAnnotations] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const generation = useRef<AbortController | null>(null);
  const annotationRequest = useRef<AbortController | null>(null);

  useEffect(() => {
    const modal = dialog.current;
    if (!modal) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Native modal dialogs enter the browser's top layer and isolate nested dialogs.
    modal.showModal(); closeButton.current?.focus();
    return () => {
      generation.current?.abort(); generation.current = null;
      annotationRequest.current?.abort(); annotationRequest.current = null;
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
    if (!result?.can_generate || result.detail || generation.current || annotationRequest.current) return;
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
  const readAnnotations = async () => {
    if (!result?.can_fetch_annotations || generation.current || annotationRequest.current) return;
    const controller = new AbortController(); annotationRequest.current = controller;
    setReadingAnnotations(true); setError('');
    try {
      const value = await request<DictionaryResult>(dictionaryAnnotationsPath(word), {
        method: 'POST', body: JSON.stringify({ refresh: Boolean(result.maimemo) }), signal: controller.signal,
      });
      if (!controller.signal.aborted) setResult(value);
    } catch (reason) { if (!controller.signal.aborted) setError(errorMessage(reason)); }
    finally {
      if (!controller.signal.aborted) { setReadingAnnotations(false); annotationRequest.current = null; }
    }
  };
  const entry = result?.dictionary;

  return createPortal(<dialog ref={dialog} className="dictionary-dialog" data-dictionary-dialog="" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1}
    onKeyDown={event => {
      if (event.key !== 'Tab') return;
      const modal = event.currentTarget;
      const elements = Array.from(modal.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), input:not(:disabled), textarea:not(:disabled), a[href], summary, [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
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
    <div className="dictionary-speech"><PronunciationButton text={word} label="朗读单词" prepareOnMount /><SpeechSettings /></div>
    <div className="dictionary-body" aria-busy={loading}>
      {loading && <p className="dictionary-loading" role="status"><LoaderCircle size={18} className="spin" />正在读取本地释义…</p>}
      {error && <div className="dictionary-error" role="alert"><p>{error}</p>{!result && <button type="button" className="text-button" onClick={() => setRetry(value => value + 1)}><RefreshCw size={14} />重新读取释义</button>}</div>}
      {result && <WordAnnotations result={result}
        annotationAction={result.can_fetch_annotations ? <div className="dictionary-annotation-actions"><button type="button" className="text-button" disabled={readingAnnotations || generating} onClick={() => void readAnnotations()}>{readingAnnotations ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}{readingAnnotations ? '正在读取墨墨个人注解…' : result.maimemo ? '更新墨墨个人注解' : '读取墨墨个人注解'}</button>{readingAnnotations && <p className="dictionary-empty" role="status">先看本地词义，墨墨个人注解稍后补充。</p>}</div> : undefined}
        afterDefinitions={<WordExpansion word={word} cache={expansionCache} vocabulary={vocabulary} onLearningChanged={onLearningChanged} />}
        detailAction={<div className="dictionary-generate"><p>补充常见义项、词性、双语例句、常用搭配和易混辨析。点击后调用 AI，成功后保存在本地，方便下次查看。</p><button type="button" className="button primary" disabled={generating || readingAnnotations || !result.can_generate} onClick={() => void generate()}>{generating ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={16} />}{generating ? '正在生成详细讲解…' : dictionaryGenerationLabel(result)}</button>{generating && <ThoughtLine label="正在整理义项和双语例句，关闭窗口可停止等待。" />}{!result.can_generate && <p className="dictionary-empty">请先完成本机 AI 服务配置；离线词典仍可正常查看。</p>}</div>}
      />}
    </div>
    <footer className="dictionary-footer">查看词典不会改变掌握状态或复习安排。<button type="button" className="text-button" disabled={assistantSuppressed} title={assistantSuppressed ? '退出专注阅读或完成当前操作后可使用学习助手' : undefined} onClick={() => { onClose(); mascot.openAssistant({ key: `word:${word.toLowerCase()}`, documentId: null, occurrenceId: null, word, title: null, sentence: null }); }}><MessageCircle size={14} />{assistantSuppressed ? '当前模式下助手已收起' : '和 Lexi 学这个词'}</button><button type="button" className="text-button" onClick={onClose}>返回学习</button></footer>
  </dialog>, document.body);
}
