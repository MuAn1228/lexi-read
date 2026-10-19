import { ArrowRight, BookOpen, Check, FilePenLine, Leaf, LoaderCircle, RefreshCw, Save, Sparkles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { ApiError, errorMessage, request } from './api';
import { PRACTICE_LENGTH_LABELS, practiceParts, practiceProblem, practiceTargetReason, usablePractice } from './practiceGeneration';
import type { PracticeDifficulty, PracticeLength, PracticeOptions, PracticePayload, PracticeResult, PracticeTarget, PracticeTargets } from './practiceGeneration';
import type { TextImportPayload } from './textArticle';
import type { ReadingDocument } from './types';
import './practiceGeneration.css';

interface GenerationAttempt { payload: PracticePayload; error: string }

export default function PracticeGenerationView({ active, disabled, onSave, onOpenDocument, onGoReader }: {
  active: boolean; disabled: boolean; onSave: (payload: TextImportPayload) => Promise<ReadingDocument>;
  onOpenDocument: (id: number) => void; onGoReader: () => void;
}) {
  const [options, setOptions] = useState<PracticeOptions>({ topic: '日常生活', difficulty: 'B1', length: 'short' });
  const [targets, setTargets] = useState<PracticeTarget[] | null>(null);
  const [targetsLoading, setTargetsLoading] = useState(false);
  const [targetError, setTargetError] = useState('');
  const [result, setResult] = useState<PracticeResult | null>(null);
  const [attempt, setAttempt] = useState<GenerationAttempt | null>(null);
  const [generating, setGenerating] = useState(false);
  const [validation, setValidation] = useState('');
  const [savePayload, setSavePayload] = useState<TextImportPayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saved, setSaved] = useState<ReadingDocument | null>(null);
  const [staleTargets, setStaleTargets] = useState(false);
  const targetGeneration = useRef(0);
  const generation = useRef(0);
  const targetController = useRef<AbortController | null>(null);
  const controller = useRef<AbortController | null>(null);
  const targetPending = useRef(false);
  const generationPending = useRef(false);
  const savePending = useRef(false);
  const mounted = useRef(true);
  const currentActive = useRef(active); currentActive.current = active;
  const preview = useRef<HTMLElement>(null);
  const locked = disabled || generating || targetsLoading || Boolean(savePayload);
  const problem = practiceProblem(options, targets ?? []);
  const validResult = usablePractice(result);

  const loadTargets = async () => {
    if (!currentActive.current || disabled || targetPending.current || generationPending.current || savePending.current || savePayload) return;
    const current = ++targetGeneration.current;
    const abort = new AbortController(); targetController.current = abort;
    targetPending.current = true; setTargetsLoading(true); setTargetError('');
    try {
      const value = await request<PracticeTargets>('/practice/targets', { signal: abort.signal });
      if (current !== targetGeneration.current || abort.signal.aborted) return;
      setTargets(value.targets); setAttempt(null); setStaleTargets(false); setValidation('');
      // The new selection starts a fresh request; an existing preview stays visible until replaced.
    } catch (e) {
      if (current === targetGeneration.current && !abort.signal.aborted) setTargetError(errorMessage(e));
    } finally {
      if (current === targetGeneration.current && !abort.signal.aborted) { targetPending.current = false; setTargetsLoading(false); targetController.current = null; }
    }
  };

  useEffect(() => {
    if (active && targets === null) void loadTargets();
    if (!active) {
      ++targetGeneration.current; targetController.current?.abort(); targetController.current = null;
      targetPending.current = false; setTargetsLoading(false);
      if (generationPending.current) {
        ++generation.current; controller.current?.abort(); controller.current = null;
        generationPending.current = false; setGenerating(false);
        setAttempt(value => value ? { ...value, error: '已暂停等待结果，设置与本次请求已保留。点击重试可取回本次短文。' } : value);
      }
    }
    // Only page transitions trigger automatic reads. Editing settings never calls AI.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, disabled]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; ++generation.current; ++targetGeneration.current; controller.current?.abort(); targetController.current?.abort(); generationPending.current = false; targetPending.current = false; };
  }, []);

  const changeOptions = (value: PracticeOptions) => {
    if (locked) return;
    setOptions(value); setAttempt(null); setValidation('');
  };
  const generate = async (retry = false) => {
    if (!active || disabled || generationPending.current || targetPending.current || savePayload || savePending.current) return;
    const issue = practiceProblem(options, targets ?? []);
    if (issue) { setValidation(issue); return; }
    const payload: PracticePayload = retry && attempt ? attempt.payload : {
      ...options, topic: options.topic.trim(), vocabulary_ids: (targets ?? []).map(target => target.id), request_id: crypto.randomUUID(),
    };
    const current = ++generation.current;
    const abort = new AbortController(); controller.current = abort;
    generationPending.current = true; setGenerating(true); setAttempt({ payload, error: '' }); setValidation(''); setSaveError(''); setStaleTargets(false);
    try {
      const value = await request<PracticeResult>('/practice/generate', { method: 'POST', body: JSON.stringify(payload), signal: abort.signal });
      if (current !== generation.current || abort.signal.aborted) return;
      if (usablePractice(value)) {
        setResult(value); setAttempt(null); setSaved(null);
        if (currentActive.current) requestAnimationFrame(() => preview.current?.scrollIntoView({ block: 'nearest', behavior: 'instant' }));
      } else {
        setAttempt({ payload, error: value.warning || '本次未获得可用短文，设置已保留，可以重试。' });
      }
    } catch (e) {
      if (current === generation.current && !abort.signal.aborted) {
        setAttempt({ payload, error: errorMessage(e) }); setStaleTargets(e instanceof ApiError && e.status === 409);
      }
    } finally {
      if (current === generation.current && !abort.signal.aborted) { generationPending.current = false; setGenerating(false); controller.current = null; }
    }
  };
  const save = async () => {
    if (!active || disabled || generating || savePending.current || !usablePractice(result) || saved) return;
    const payload = savePayload ?? { request_id: crypto.randomUUID(), title: `AI练习 · ${result.title}`, text: result.text };
    // Preserve this exact receipt before sending. An uncertain response must only retry this import.
    setSavePayload(payload); savePending.current = true; setSaving(true); setSaveError('');
    try {
      const value = await onSave(payload);
      if (!mounted.current) return;
      setSaved(value); setSavePayload(null);
    } catch (e) {
      if (mounted.current) setSaveError(`${errorMessage(e)} 短文已保留，请重试确认这次保存。`);
    } finally { savePending.current = false; if (mounted.current) setSaving(false); }
  };

  return <section className="practice-generation" aria-label="个性化短文练习" hidden={!active}>
    <div className="practice-intro"><span className="practice-intro-symbol"><Leaf size={22} /></span><div><h2>让你的生词，在新故事里再见一次。</h2><p>优先选取已到复习时间的词，再补入不认识、不确定的词。生成后先预览，喜欢再保存到文章库。</p></div></div>
    <div className="practice-grid">
      <section className="panel practice-settings" aria-labelledby="practice-settings-title">
        <div className="panel-heading"><div><Sparkles size={17} /><h2 id="practice-settings-title">为你准备一篇短文</h2></div><span className="tiny-badge">来自你的词库</span></div>
        <div className="practice-settings-body">
          <div className="practice-step-label"><h3>本篇目标词</h3><span>{targets?.length ?? 0} / 5 个</span></div>
          <p className="practice-help">按真实复习时间与掌握状态挑选，不会因为生成短文就自动算作已复习。</p>
          {targetsLoading && <p className="practice-status" role="status"><LoaderCircle size={15} className="spin" />正在读取你的目标词…</p>}
          {targetError && <div className="practice-local-error" role="alert"><p>目标词暂未读取：{targetError}</p><button type="button" className="text-button" disabled={locked} onClick={() => void loadTargets()}><RefreshCw size={14} />重试读取目标词</button></div>}
          {Boolean(targets?.length) && <div className="practice-targets" aria-label="来自词库的目标词">{targets!.map(target => <span className="practice-target" key={target.id}><span className="practice-target-word"><Check size={13} /><span lang="en">{target.word}</span></span><span className="practice-target-reason">{practiceTargetReason(target)}</span></span>)}</div>}
          {targets?.length === 0 && <div className="practice-empty-targets" role="status"><h4>先留下一些想练习的词</h4><p>目前没有到期或不熟悉的词。阅读时把词标记为「不认识」或「不确定」，也可以等已有词到期后再来。</p><button type="button" className="text-button" disabled={disabled} onClick={onGoReader}><BookOpen size={14} />去阅读并标记生词<ArrowRight size={13} /></button></div>}
          {targets !== null && <button type="button" className="text-button" disabled={locked} onClick={() => void loadTargets()}><RefreshCw size={13} />刷新目标词</button>}
          <form onSubmit={event => { event.preventDefault(); void generate(Boolean(attempt)); }}>
            <fieldset className="practice-options" disabled={locked}>
              <label htmlFor="practice-topic">想读的主题 <span>{Array.from(options.topic.trim()).length}/100</span></label>
              <input id="practice-topic" value={options.topic} onChange={event => changeOptions({ ...options, topic: event.target.value })} placeholder="例如：城市散步、科技与生活、一次旅行" aria-invalid={Array.from(options.topic.trim()).length > 100} />
              <div className="practice-option-row"><div className="practice-option-field"><label htmlFor="practice-difficulty">目标难度</label><select id="practice-difficulty" value={options.difficulty} onChange={event => changeOptions({ ...options, difficulty: event.target.value as PracticeDifficulty })}><option value="A2">A2 · 基础表达</option><option value="B1">B1 · 日常阅读</option><option value="B2">B2 · 进阶阅读</option></select></div><div className="practice-option-field"><label htmlFor="practice-length">短文长度</label><select id="practice-length" value={options.length} onChange={event => changeOptions({ ...options, length: event.target.value as PracticeLength })}>{(['short', 'medium', 'long'] as const).map(length => <option key={length} value={length}>{PRACTICE_LENGTH_LABELS[length]}</option>)}</select></div></div>
              <button type="submit" className="button primary practice-submit" disabled={locked || Boolean(problem) || staleTargets}><Sparkles size={16} />{generating ? '正在生成短文…' : attempt ? '重试本次生成' : result ? '再生成一篇' : '生成我的练习短文'}</button>
            </fieldset>
          </form>
          {validation && <p className="practice-local-error" role="alert">{validation}</p>}
          {generating && <p className="practice-status" role="status"><LoaderCircle size={15} className="spin" />正在围绕这些词写一篇新的英文短文…</p>}
          {!generating && attempt?.error && <div className="practice-local-error" role="alert"><p>{attempt.error}</p>{staleTargets ? <button type="button" className="text-button" disabled={locked} onClick={() => void loadTargets()}><RefreshCw size={14} />刷新目标词后重新生成</button> : <p className="practice-help">设置未改动时，重试会继续同一次生成。</p>}</div>}
          {savePayload && <p className="practice-local-error" role="status">{saving ? '正在保存这篇短文…' : '这次保存的结果尚未确认。请在右侧重试保存，确认前已保留短文并暂停重新生成。'}</p>}
          <p className="practice-footnote">点击生成才会调用 AI。难度是本次写作目标，供阅读参考；不会据此评定你的英语水平。</p>
        </div>
      </section>
      <section className="panel practice-preview" aria-labelledby="practice-preview-title" ref={preview}>
        <div className="panel-heading"><div><FilePenLine size={17} /><h2 id="practice-preview-title">短文预览</h2></div><span className="tiny-badge">{saved ? '已保存到文章库' : '保存前不会加入文章库'}</span></div>
        {validResult ? <>
          <article className="practice-preview-paper"><div className="practice-preview-meta"><span>AI 生成练习</span><span>目标 {result.difficulty}</span><span>{result.word_count ?? '—'} 词</span>{result.cached && <span>已取回本次生成</span>}</div><h3 lang="en">{result.title}</h3><p className="practice-preview-text" lang="en">{practiceParts(result.text, result.matches, result.targets).map((part, index) => part.vocabularyId === null ? <span key={index}>{part.text}</span> : <mark key={index}>{part.text}</mark>)}</p><div className="practice-coverage"><p>已在文中实际出现的目标词 · {result.targets.length} 个</p><div className="practice-targets">{result.targets.map(target => <span className="practice-covered-word" lang="en" key={target.id}>{target.word}</span>)}</div></div>{result.warning && <p className="practice-help">{result.warning}</p>}</article>
          <div className="practice-save-bar"><p>{saved ? '已成为一篇文字文章，可在文章库再次打开，继续查词、问答与标记。' : '保存后会按文章中的实际出现记录遇见次数，可继续查词、AI 问答与标记。生成和预览本身不会更改词库或复习排期。'}</p><div className="practice-save-actions">{saved ? <><span className="practice-saved"><Check size={15} />已保存</span><button type="button" className="button primary" disabled={disabled} onClick={() => onOpenDocument(saved.id)}><BookOpen size={15} />打开这篇短文<ArrowRight size={14} /></button></> : <button type="button" className="button primary" disabled={disabled || generating || saving || targetsLoading} onClick={() => void save()}>{saving ? <LoaderCircle size={15} className="spin" /> : <Save size={15} />}{saving ? '正在保存…' : savePayload ? '重试保存并开始阅读' : '保存并开始阅读'}{!saving && <ArrowRight size={14} />}</button>}</div>{saveError && <p className="practice-local-error" role="alert">{saveError}</p>}</div>
        </> : <div className="practice-preview-empty"><BookOpen size={36} strokeWidth={1.2} /><h3>A new story,<br />with your own words.</h3><p>选一个感兴趣的主题，让已经遇见的词进入新的语境。生成的短文会显示在这里。</p></div>}
      </section>
    </div>
    <p className="practice-footnote">未保存的短文和设置仅保留在当前页面，切换导航可继续，刷新或关闭页面会清空。AI 练习内容请结合语境核对。</p>
  </section>;
}
