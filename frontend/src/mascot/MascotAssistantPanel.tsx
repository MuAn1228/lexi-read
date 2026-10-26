import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { BookOpen, Check, CircleAlert, HelpCircle, MessageCircle, RefreshCw, Send, Square, Trash2, WandSparkles, X } from 'lucide-react';
import { errorMessage } from '../api';
import { requestAIStream } from '../aiStream';
import type { AIStreamPhase } from '../aiStream';
import AIWaitPanel from '../components/AIWaitPanel';
import { createMascotOperationId } from './mascotEvents';
import { useMascot } from './useMascot';
import { ASSISTANT_MESSAGE_LIMIT, ASSISTANT_TURN_LIMIT, assistantHistory, assistantMessageProblem, assistantPanelPlacement, assistantPayload, assistantQuickQuestion, assistantResponse } from './mascotAssistant';
import type { AssistantHistory, AssistantMode, AssistantQuiz, AssistantReadingContext, AssistantTurn } from './mascotAssistant';
import type { MascotRect, MascotViewport } from './mascotPosition';
import './mascotAssistant.css';

interface Attempt { message: string; mode: AssistantMode; history: AssistantHistory[]; error: string }
type Pending = { controller: AbortController; operationId: string; key: string; sequence: number; message: string };

function Quiz({ quiz, answered, onAnswer }: { quiz: AssistantQuiz; answered?: number; onAnswer: (choice: number) => void }) {
  const [selected, setSelected] = useState<number | null>(null);
  const revealed = answered !== undefined;
  return <div className="mascot-assistant-quiz" aria-label="英语小练习">
    <p className="mascot-assistant-quiz-question">{quiz.question}</p>
    <div className="mascot-assistant-quiz-options">{quiz.options.map((option, index) => <button type="button" key={index} disabled={revealed} aria-pressed={selected === index} className={`${revealed && index === quiz.answer_index ? ' is-correct' : ''}${revealed && answered === index && index !== quiz.answer_index ? ' is-incorrect' : ''}`} onClick={() => setSelected(index)}><span>{'ABCD'[index]}</span>{option}{revealed && index === quiz.answer_index && <Check size={13} aria-hidden="true" />}</button>)}</div>
    {!revealed ? <button className="mascot-assistant-check" type="button" disabled={selected === null} onClick={() => { if (selected !== null) onAnswer(selected); }}>确认答案</button>
      : <div className="mascot-assistant-quiz-result" role="status"><strong>{answered === quiz.answer_index ? '答对了！' : `正确答案是 ${'ABCD'[quiz.answer_index]}`}</strong><p>{quiz.explanation}</p><small>练习仅供巩固，不会修改词库掌握状态。</small></div>}
  </div>;
}

export default function MascotAssistantPanel({ context, disabled, onClose, anchor, viewport }: {
  context: AssistantReadingContext; disabled: boolean; onClose: () => void; anchor: MascotRect; viewport: MascotViewport;
}) {
  const mascot = useMascot();
  const panel = useRef<HTMLElement>(null), input = useRef<HTMLTextAreaElement>(null), log = useRef<HTMLDivElement>(null);
  const [draft, setDraft] = useState(''), [validation, setValidation] = useState('');
  const [turns, setTurns] = useState<AssistantTurn[]>([]), [attempt, setAttempt] = useState<Attempt | null>(null);
  const [loading, setLoading] = useState(false);
  const [phase, setPhase] = useState<AIStreamPhase | undefined>();
  const [preview, setPreview] = useState('');
  const followBottom = useRef(true);
  const pending = useRef<Pending | null>(null), sequence = useRef(0), nextTurn = useRef(0);
  const contextRef = useRef(context), onCloseRef = useRef(onClose), disabledRef = useRef(disabled);
  contextRef.current = context; onCloseRef.current = onClose; disabledRef.current = disabled;
  const inputId = useId(), hintId = useId();
  const full = turns.length >= ASSISTANT_TURN_LIMIT;
  const length = Array.from(draft.trim()).length;
  const placement = assistantPanelPlacement(anchor, viewport);

  const cancel = useCallback((clearAttempt = false) => {
    ++sequence.current;
    const current = pending.current; pending.current = null;
    current?.controller.abort();
    if (current) mascot.cancelOperation(current.operationId);
    setLoading(false); setPreview(''); setPhase(undefined);
    if (current && !clearAttempt) setDraft(value => value || current.message);
    if (clearAttempt) setAttempt(null);
    else if (current) setAttempt(value => value ? { ...value, error: '已停止生成，可以重试或修改问题。' } : null);
  }, [mascot]);
  const close = () => { cancel(); onCloseRef.current(); };
  useEffect(() => {
    input.current?.focus({ preventScroll: true });
    const keys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onCloseRef.current(); return; }
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !['1', '2', '3', 'ArrowDown'].includes(event.key)) return;
      const editable = event.target instanceof Element && event.target.closest('input,textarea,select,[contenteditable="true"],[contenteditable=""]');
      if (!editable) { event.preventDefault(); event.stopPropagation(); }
      else if (event.target instanceof Node && panel.current?.contains(event.target)) event.stopPropagation();
    };
    document.addEventListener('keydown', keys, true);
    return () => { document.removeEventListener('keydown', keys, true); ++sequence.current; const current = pending.current; pending.current = null; current?.controller.abort(); if (current) mascot.cancelOperation(current.operationId); };
  }, [mascot]);
  useEffect(() => {
    cancel(true); setTurns([]); setDraft(''); setValidation('');
    // Context identity is supplied by the app; changing an unrelated display string cannot reset a session.
  }, [context.key, cancel]);
  useEffect(() => { if (disabled) cancel(); }, [disabled, cancel]);
  useLayoutEffect(() => { if (log.current && followBottom.current) log.current.scrollTop = log.current.scrollHeight; }, [turns.length, loading, attempt?.error, preview, phase]);

  const submit = async (message: string, mode: AssistantMode, history = assistantHistory(turns)) => {
    if (disabledRef.current || pending.current || turns.length >= ASSISTANT_TURN_LIMIT) return;
    const problem = assistantMessageProblem(message);
    if (problem) { setValidation(problem); input.current?.focus({ preventScroll: true }); return; }
    const currentContext = contextRef.current;
    let payload;
    try { payload = assistantPayload(currentContext, message, mode, history); }
    catch (error) { setValidation(errorMessage(error)); return; }
    const controller = new AbortController(), operationId = createMascotOperationId('assistant');
    const current: Pending = { controller, operationId, key: currentContext.key, sequence: ++sequence.current, message: message.trim() };
    pending.current = current;
    const next = { message: message.trim(), mode, history, error: '' };
    followBottom.current = true;
    setAttempt(next); setLoading(true); setValidation(''); setDraft(''); setPreview(''); setPhase(undefined);
    const word = currentContext.word || '英语学习';
    mascot.emit({ type: 'ANALYSIS_STARTED', operationId, word });
    const isCurrent = () => pending.current === current && sequence.current === current.sequence && contextRef.current.key === current.key && !controller.signal.aborted && !disabledRef.current;
    try {
      const value = await requestAIStream<unknown>('/assistant/messages/stream', { method: 'POST', body: JSON.stringify(payload), signal: controller.signal }, {
        onStatus: value => { if (isCurrent()) setPhase(value); },
        onPreview: text => { if (isCurrent() && mode !== 'quiz') setPreview(text); },
      });
      if (!isCurrent()) return;
      const response = assistantResponse(value, mode);
      if (!response) throw new Error('AI 返回的内容不完整，请重新试一次。');
      if (response.source === 'unavailable') {
        setAttempt({ ...next, error: response.warning || 'Agnes 暂时无法回答，请稍后重试。' });
        setDraft(value => value || next.message); mascot.emit({ type: 'ANALYSIS_ERROR', operationId, word });
      } else {
        setTurns(items => [...items, { id: ++nextTurn.current, question: next.message, mode, response }]);
        setAttempt(null); mascot.emit({ type: 'ANALYSIS_SUCCESS', operationId, word, source: 'llm' });
      }
    } catch (error) {
      if (!isCurrent()) return;
      setAttempt({ ...next, error: errorMessage(error) }); setDraft(value => value || next.message);
      mascot.emit({ type: 'ANALYSIS_ERROR', operationId, word });
    } finally {
      if (pending.current === current) { pending.current = null; setLoading(false); setPreview(''); setPhase(undefined); }
    }
  };
  const clear = () => { cancel(true); setTurns([]); setDraft(''); setValidation(''); input.current?.focus({ preventScroll: true }); };

  return <section ref={panel} className="mascot-assistant-panel" data-testid="mascot-assistant-panel" data-compact={placement.height < 420} role="group" aria-label="Lexi Agnes 学习助手" style={{ left: placement.x, top: placement.y, width: placement.width, height: placement.height }} onKeyDown={event => event.stopPropagation()} onKeyUp={event => event.stopPropagation()}>
    <header className="mascot-assistant-heading"><div><span className="mascot-assistant-brand"><MessageCircle size={16} />Lexi <span>· Agnes</span></span><p>{context.word ? `正在学 ${context.word}` : '英语学习助手'}</p></div><div>{loading && <button type="button" aria-label="停止生成" title="停止生成" onClick={() => cancel()}><Square size={13} /></button>}<button type="button" aria-label="清空助手对话" title="清空本轮对话" onClick={clear} disabled={!turns.length && !attempt}><Trash2 size={15} /></button><button type="button" aria-label="关闭学习助手" onClick={close}><X size={17} /></button></div></header>
    <div className="mascot-assistant-context"><BookOpen size={14} aria-hidden="true" /><div><strong>{context.word || context.title || '随时问一个英语问题'}</strong>{context.word && context.title && <span>{context.title}</span>}{context.sentence ? <details><summary>当前原句</summary><p lang="en">{context.sentence}</p></details> : <p>{context.documentId ? '结合当前文章学习，也可以继续追问。' : '词义、用法、记忆方法，都可以聊。'}</p>}</div></div>
    <div className="mascot-assistant-log" ref={log} onScroll={() => { const node = log.current; if (node) followBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 40; }} role="log" aria-label="学习助手对话" aria-live="polite" aria-relevant="additions">
      {!turns.length && !attempt && <div className="mascot-assistant-empty"><WandSparkles size={24} /><h3>{context.word ? `一起弄懂 ${context.word}` : '把问题交给 Lexi'}</h3><p>讲解、记忆、练习。每次点击后才会向 Agnes 提问。</p></div>}
      {turns.map(turn => <article className="mascot-assistant-turn" key={turn.id}><p className="mascot-assistant-question">{turn.question}</p><div className="mascot-assistant-answer"><span className="mascot-assistant-speaker">Lexi</span><p>{turn.response.answer}</p>{turn.response.quiz && <Quiz quiz={turn.response.quiz} answered={turn.quizChoice} onAnswer={choice => setTurns(items => items.map(item => item.id === turn.id ? { ...item, quizChoice: choice } : item))} />}{turn.response.context_limited && <small>文章较长，本次参考了部分相关片段。</small>}{turn.response.warning && <small>{turn.response.warning}</small>}</div></article>)}
      {attempt && <article className="mascot-assistant-turn"><p className="mascot-assistant-question">{attempt.message}</p>{loading ? <AIWaitPanel label="Agnes 正在整理回答…" phase={phase} preview={attempt.mode === 'quiz' ? undefined : preview} onStop={() => cancel()} compact /> : <div className="mascot-assistant-failure" role="alert"><CircleAlert size={14} /><div><p>{attempt.error}</p><button type="button" disabled={disabled || full} onClick={() => void submit(attempt.message, attempt.mode, attempt.history)}><RefreshCw size={13} />重试这次提问</button></div></div>}</article>}
    </div>
    <footer className="mascot-assistant-footer">
      <div className="mascot-assistant-shortcuts" aria-label="Agnes 学习功能">{([{ mode: 'explain', label: '讲解词句', icon: BookOpen }, { mode: 'memory', label: '帮我记住', icon: WandSparkles }, { mode: 'quiz', label: '出一道题', icon: HelpCircle }] as const).map(({ mode, label, icon: Icon }) => <button type="button" key={mode} disabled={disabled || loading || full} onClick={() => void submit(assistantQuickQuestion(mode, contextRef.current), mode)}><Icon size={13} />{label}</button>)}</div>
      {full ? <div className="mascot-assistant-limit" role="status"><p>本轮已完成 {ASSISTANT_TURN_LIMIT} 次提问。</p><button type="button" onClick={clear}>开始新一轮</button></div> : <form onSubmit={event => { event.preventDefault(); void submit(draft, 'chat'); }}>
        <label htmlFor={inputId} className="mascot-assistant-input-label">向 Lexi 提问</label><textarea ref={input} id={inputId} aria-label="向 Lexi 提问" aria-describedby={hintId} aria-invalid={Boolean(validation) || length > ASSISTANT_MESSAGE_LIMIT} value={draft} disabled={disabled} rows={2} placeholder="这个词怎么用？也可以问一个新问题…" onChange={event => { setDraft(event.target.value); setValidation(''); }} onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(draft, 'chat'); } }} />
        <div className="mascot-assistant-compose-actions"><span id={hintId}>{length}/{ASSISTANT_MESSAGE_LIMIT}<span> · Ctrl + Enter 发送</span></span>{loading ? <button type="button" className="mascot-assistant-send is-stop" onClick={() => cancel()}><Square size={12} />停止</button> : <button type="submit" className="mascot-assistant-send" disabled={disabled || !draft.trim() || length > ASSISTANT_MESSAGE_LIMIT}><Send size={13} />发送</button>}</div>
        {validation && <p className="mascot-assistant-validation" role="alert">{validation}</p>}
      </form>}
      <p className="mascot-assistant-footnote">对话仅保留在本轮，关闭或切换词句后清空。</p>
    </footer>
  </section>;
}
