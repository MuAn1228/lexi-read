import { BookOpen, CornerDownRight, MessageCircle, RefreshCw, Sparkles } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { errorMessage, request } from './api';
import { ARTICLE_QUESTION_LIMIT, ARTICLE_TURN_LIMIT, articleQuestionHistory, articleQuestionProblem, validArticleFocus } from './articleQuestions';
import type { ArticleAnswer, ArticleCitation, ArticleHistory, ArticleTurn } from './articleQuestions';
import type { ReadingDocument } from './types';
import { FlyingSendIcon, PaperCrumpleIcon } from './components/StudyInteractions';
import AIWaitPanel from './components/AIWaitPanel';
import './articleQuestionPanel.css';

const prompts = ['请用中文概括这篇文章的主要内容。', '作者的核心观点是什么？有哪些依据？', '这篇文章有哪些值得留意的细节？'];
interface Attempt { question: string; history: ArticleHistory[]; error: string; response: ArticleAnswer | null }

export default function ArticleQuestionPanel({ document, disabled, onLocate, onClear }: {
  document: ReadingDocument; disabled: boolean; onLocate: (citation: ArticleCitation) => void; onClear: () => void;
}) {
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<ArticleTurn[]>([]);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [loading, setLoading] = useState(false);
  const [validation, setValidation] = useState('');
  const [flight, setFlight] = useState(0);
  const [crumple, setCrumple] = useState(0);
  const generation = useRef(0);
  const pending = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const turnId = useRef(0);
  const latest = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const inputId = useId();
  const hintId = useId();
  const isFull = turns.length >= ARTICLE_TURN_LIMIT;
  const emptyArticle = !document.ocr_text.trim();
  const locked = disabled || loading || isFull || emptyArticle;
  const questionLength = Array.from(draft.trim()).length;

  useEffect(() => () => { ++generation.current; controller.current?.abort(); }, []);
  useEffect(() => {
    if (attempt || turns.length) latest.current?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  }, [turns.length, loading]);

  const submit = async (question: string, history: ArticleHistory[]) => {
    if (disabled || pending.current || isFull || emptyArticle) return;
    const problem = articleQuestionProblem(question);
    if (problem) { setValidation(problem); input.current?.focus(); return; }
    const current = ++generation.current;
    const abort = new AbortController(); controller.current = abort;
    const next: Attempt = { question: question.trim(), history, error: '', response: null };
    pending.current = true; setLoading(true); setAttempt(next); setValidation(''); setFlight(value => value + 1);
    try {
      const response = await request<ArticleAnswer>(`/documents/${document.id}/questions`, {
        method: 'POST', body: JSON.stringify({ question: next.question, history }), signal: abort.signal,
      });
      if (current !== generation.current || abort.signal.aborted) return;
      if (response.source === 'llm' && response.answer?.trim()) {
        setTurns(items => [...items, { id: ++turnId.current, question: next.question, response }]);
        setAttempt(null); setDraft('');
      } else {
        setAttempt({ ...next, response });
        setDraft(next.question);
      }
    } catch (e) {
      if (current === generation.current && !abort.signal.aborted) { setAttempt({ ...next, error: errorMessage(e) }); setDraft(next.question); }
    } finally {
      if (current === generation.current && !abort.signal.aborted) { pending.current = false; setLoading(false); controller.current = null; }
    }
  };
  const stop = () => {
    ++generation.current; controller.current?.abort(); controller.current = null; pending.current = false;
    setLoading(false);
    if (attempt) setDraft(value => value || attempt.question);
    setAttempt(value => value ? { ...value, error: '已停止等待，问题已保留，可以重试。' } : value);
  };
  const clear = () => {
    ++generation.current; controller.current?.abort(); controller.current = null; pending.current = false;
    setLoading(false); setTurns([]); setAttempt(null); setDraft(''); setValidation(''); setCrumple(value => value + 1); onClear();
    input.current?.focus();
  };
  const ask = (question: string) => void submit(question, articleQuestionHistory(turns));
  const canLocate = (citation: ArticleCitation) => validArticleFocus(document, { text: citation.quote, start: citation.text_start, end: citation.text_end, token: 0 });

  return <section className="article-questions panel" aria-label="AI 文章问答">
    <div className="panel-heading"><div><MessageCircle size={17} /><h2>AI 文章问答</h2><span className="tiny-badge">围绕当前文章</span></div><button type="button" className="text-button" disabled={disabled || (!turns.length && !attempt && !draft)} onClick={clear}><PaperCrumpleIcon run={crumple} />清空问答</button></div>
    <div className="article-question-body">
      <div className="article-question-intro"><span className="article-question-symbol"><Sparkles size={20} /></span><div><h3>把没读懂的地方，接着问下去。</h3><p>根据这篇文章回答，附上可核对的原文。支持中文或英文提问。</p></div></div>
      {emptyArticle ? <p className="article-question-note" role="status">这篇文章还没有可用于问答的正文，请先导入可识别的文章。</p> : <>
        {!turns.length && !attempt && <div className="article-question-prompts" aria-label="文章问答预设问题">{prompts.map((prompt, index) => <button type="button" disabled={locked} key={prompt} onClick={() => { setDraft(prompt); ask(prompt); }}><span>{['概括主要内容', '梳理观点与依据', '找出关键细节'][index]}</span><CornerDownRight size={14} /></button>)}</div>}
        <div className="article-question-conversation" role="log" aria-label="本篇文章问答记录" aria-relevant="additions" aria-live="polite">
          {turns.map((turn, index) => <article className="article-question-turn" key={turn.id}>
            <div className="article-question-user"><span className="article-question-number">{String(index + 1).padStart(2, '0')}</span><p>{turn.question}</p></div>
            <div className={`article-question-answer ${turn.response.answerable === false ? 'article-question-unsupported' : ''}`}>
              <div className="article-question-answer-label"><Sparkles size={13} /><span>{turn.response.answerable === false ? '原文依据不足' : '根据文章回答'}</span>{turn.response.cached && <span className="article-question-cache">已复用回答</span>}</div>
              <p className="article-question-answer-text">{turn.response.answer}</p>
              {turn.response.context_limited && <p className="article-question-note">本次根据文章相关片段回答，未覆盖全文。</p>}
              {turn.response.warning && <p className="article-question-note">{turn.response.warning}</p>}
              {turn.response.citations.length > 0 && <details className="article-question-citations"><summary><BookOpen size={14} />查看原文依据 <span>{turn.response.citations.length}</span></summary><ol>{turn.response.citations.map((citation, number) => <li key={`${citation.chunk_id}-${number}`}><div className="article-question-citation-label"><span>依据 {number + 1}{citation.page_number !== null ? ` · 第 ${citation.page_number} 页` : ''}</span><button type="button" className="text-button" disabled={disabled || !canLocate(citation)} onClick={() => onLocate(citation)} aria-label={`定位第 ${index + 1} 轮的第 ${number + 1} 处原文`}>定位原文<CornerDownRight size={13} /></button></div><blockquote lang="en">{citation.quote}</blockquote>{!canLocate(citation) && <p className="article-question-note">正文已变化，暂时无法定位这处引用。</p>}</li>)}</ol></details>}
            </div>
          </article>)}
          {attempt && <article className="article-question-turn article-question-pending"><div className="article-question-user"><span className="article-question-number">{String(turns.length + 1).padStart(2, '0')}</span><p>{attempt.question}</p></div>{loading ? <AIWaitPanel label="正在阅读原文，整理回答与依据…" onStop={stop} /> : <div className="article-question-failure" role={attempt.error ? 'alert' : 'status'}><p>{attempt.error || attempt.response?.warning || '文章问答暂不可用，请检查模型服务后重试。'}</p><button type="button" className="text-button" disabled={disabled} onClick={() => void submit(attempt.question, attempt.history)}><RefreshCw size={14} />重试这个问题</button></div>}</article>}
          <div ref={latest} />
        </div>
        {isFull ? <div className="article-question-limit" role="status"><p>本轮已完成 {ARTICLE_TURN_LIMIT} 次提问。清空问答后，可以开始新一轮。</p><button type="button" className="button secondary" disabled={disabled} onClick={clear}>清空并重新提问</button></div> : <form className="article-question-form" onSubmit={event => { event.preventDefault(); ask(draft); }}>
          <label htmlFor={inputId}>{turns.length ? '继续追问' : '你的问题'}</label>
          <textarea id={inputId} ref={input} value={draft} disabled={locked} rows={3} aria-describedby={hintId} aria-invalid={Boolean(validation) || questionLength > ARTICLE_QUESTION_LIMIT} placeholder={turns.length ? '可以接着刚才的回答问，也可以问文章中的其他内容…' : '例如：为什么作者认为这个方法有效？'} onChange={event => { setDraft(event.target.value); setValidation(''); }} />
          <div className="article-question-form-footer"><p id={hintId}>{turns.length ? `本轮 ${turns.length}/${ARTICLE_TURN_LIMIT} · 追问参考最近 3 次有依据的回答` : '点击提问后才会调用 AI'}<span className={questionLength > ARTICLE_QUESTION_LIMIT ? 'over-limit' : ''}>{questionLength}/{ARTICLE_QUESTION_LIMIT}</span></p><button type="submit" className="button primary" disabled={locked || !draft.trim() || questionLength > ARTICLE_QUESTION_LIMIT}><FlyingSendIcon run={flight} />{turns.length ? '发送追问' : '提问'}</button></div>
          {validation && <p className="article-question-validation" role="alert">{validation}</p>}
        </form>}
        <p className="article-question-footnote">问答仅保留在当前阅读页，离开页面或切换文章后清空。回答请结合原文核对。</p>
      </>}
    </div>
  </section>;
}
