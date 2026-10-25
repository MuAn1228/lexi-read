import { ArrowRight, BookOpen, Check, Eye, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode, Ref } from 'react';
import { errorMessage, request } from './api';
import { matchesReviewAnswer, reviewCloze } from './reviewExercise';
import { useDictionary } from './DictionaryPanel';
import ReviewAnnotations from './ReviewAnnotations';
import type { ReviewMode } from './reviewExercise';
import type { ReviewItem, ReviewMeaning } from './types';
import './reviewPractice.css';

export default function ReviewPractice({ item, mode, onModeChange, autoExplain, onAutoExplainChange, onGoToRating, disabled, active, onReveal, onOpenDocument, headingRef, meta }: {
  item: ReviewItem; mode: ReviewMode; onModeChange: (mode: ReviewMode) => void;
  disabled: boolean; active: boolean; onReveal: () => void;
  autoExplain: boolean; onAutoExplainChange: (value: boolean) => void;
  onGoToRating: () => void;
  onOpenDocument: (documentId: number, occurrenceId: number) => void;
  headingRef: Ref<HTMLHeadingElement>; meta: ReactNode;
}) {
  const openDictionary = useDictionary();
  const [revealed, setRevealed] = useState(false);
  const [input, setInput] = useState('');
  const [correct, setCorrect] = useState<boolean | null>(null);
  const [meaning, setMeaning] = useState<ReviewMeaning | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const pending = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const cloze = reviewCloze(item.occurrence);
  const isCloze = mode === 'cloze' && Boolean(cloze);
  const occurrence = item.occurrence;
  useEffect(() => {
    if (!active) {
      ++generation.current; controller.current?.abort(); controller.current = null;
      pending.current = false; setLoading(false);
    }
    return () => { ++generation.current; controller.current?.abort(); };
  }, [active]);

  const loadMeaning = async () => {
    if (disabled || !active || pending.current || !occurrence) return;
    const current = ++generation.current;
    const abort = new AbortController(); controller.current = abort;
    pending.current = true; setLoading(true); setError('');
    try {
      const result = await request<ReviewMeaning>('/review/meaning', {
        method: 'POST', body: JSON.stringify({ vocabulary_id: item.vocabulary.id, occurrence_id: occurrence.id }), signal: abort.signal,
      });
      if (current === generation.current && !abort.signal.aborted) setMeaning(result);
    } catch (e) {
      if (current === generation.current && !abort.signal.aborted) setError(errorMessage(e));
    } finally {
      if (current === generation.current && !abort.signal.aborted) { pending.current = false; setLoading(false); controller.current = null; }
    }
  };
  const reveal = (checkAnswer = false) => {
    if (disabled || !active || revealed || (checkAnswer && !input.trim())) return;
    if (checkAnswer && cloze) setCorrect(matchesReviewAnswer(input, cloze.answer));
    setRevealed(true); onReveal();
  };

  return <div className="review-practice">
    <div className="review-mode" role="group" aria-label="复习练习方式">
      <button type="button" aria-pressed={!isCloze} disabled={disabled} onClick={() => onModeChange('recall')}>词卡回想</button>
      <button type="button" aria-pressed={isCloze} disabled={disabled || !cloze} onClick={() => onModeChange('cloze')}>原句填空</button>
    </div>
    {!cloze && occurrence && <p className="review-practice-note">这张卡没有可精确定位的原句，使用词卡回想。</p>}
    <p className="overline">{isCloze ? '结合原文，填回空缺处的单词' : '先回想这个词的意思，再揭晓释义'}</p>
    <h3 className={`review-word${isCloze && !revealed ? ' review-cloze-heading' : ''}`} ref={headingRef} tabIndex={-1}>{isCloze && !revealed ? '填回原句中的词' : isCloze ? occurrence?.word : item.vocabulary.word}</h3>
    {(!isCloze || revealed) && item.vocabulary.lemma !== (isCloze ? occurrence?.word : item.vocabulary.word)?.toLowerCase() && <p className="review-lemma">词元 · {item.vocabulary.lemma}</p>}
    {meta}
    {isCloze && cloze && <div className="review-cloze">
      <p className="section-label">原句填空</p>
      <blockquote lang="en">{cloze.before}<span className="review-blank" aria-label="待填写的单词">______</span>{cloze.after}</blockquote>
      <form onSubmit={event => { event.preventDefault(); reveal(true); }}>
        <label htmlFor="review-answer">填入原文中的单词</label>
        <input id="review-answer" value={input} onChange={event => setInput(event.target.value)} disabled={disabled} readOnly={revealed} maxLength={Math.max(256, cloze.answer.length)} autoComplete="off" autoCapitalize="none" autoCorrect="off" spellCheck={false} aria-describedby="review-answer-note" />
        <p id="review-answer-note">按原文词形填写，忽略大小写、首尾空格和弯直撇号差异。练习结果供参考，复习安排由你的自评决定。</p>
        {!revealed && <div className="review-exercise-actions"><button className="button primary" type="submit" disabled={disabled || !input.trim()}><Check size={15} />检查答案</button><button className="text-button" type="button" disabled={disabled} onClick={() => reveal()}><Eye size={15} />直接揭晓答案</button></div>}
      </form>
      {revealed && <div className={`review-answer-feedback ${correct === null ? 'revealed' : correct ? 'correct' : 'different'}`} role="status">
        {correct !== null && <p>{correct ? <Check size={16} /> : <X size={16} />}{correct ? '和原文一致。' : '与原文不同，可以再留意一下这个词形。'}</p>}
        <p>原文答案：<strong lang="en">{cloze.answer}</strong></p>
      </div>}
    </div>}
    {!revealed && !isCloze && <div className="review-context-actions"><button className="button secondary" disabled={disabled} onClick={() => reveal()}><Eye size={15} />揭晓释义</button></div>}
    <ReviewAnnotations word={item.vocabulary.word} revealed={revealed} active={active} disabled={disabled} summary={item.vocabulary.meaning_summary} autoExplain={autoExplain} onAutoExplainChange={onAutoExplainChange} onGoToRating={onGoToRating} />
    {revealed && <section className="review-revealed" aria-label="本词释义与原句">
      <button type="button" className="text-button" disabled={disabled} onClick={event => openDictionary(item.vocabulary.word, event.currentTarget)}><BookOpen size={15} />打开词典与词汇拓展<ArrowRight size={14} /></button>
      {occurrence && <div className="review-meaning">
        <div className="section-label"><span>本句中的意思</span>{meaning && meaning.source !== 'unavailable' && <span className={`source-badge ${meaning.source}`}>{meaning.source === 'llm' ? 'AI 语境释义' : '离线基础释义'}</span>}</div>
        {loading && <p className="loading-copy" role="status"><LoaderCircle size={16} className="spin" />正在获取本词释义…</p>}
        {error && <div className="inline-error" role="alert"><p>{error} 已保留本卡答案，可重试或根据记忆自评。</p><button className="text-button" disabled={disabled || loading} onClick={() => void loadMeaning()}><RefreshCw size={14} />重试释义</button></div>}
        {meaning?.source === 'unavailable' && <div className="review-meaning-unavailable" role="status"><p>{meaning.warning || '暂时没有可靠释义，可重试或回到原文查看。'}</p><button className="text-button" disabled={disabled || loading} onClick={() => void loadMeaning()}><RefreshCw size={14} />重试释义</button></div>}
        {meaning && meaning.source !== 'unavailable' && <>
          {meaning.part_of_speech && <p className="review-meaning-pos">{meaning.part_of_speech}</p>}
          {meaning.meaning_in_context && <h4>{meaning.meaning_in_context}</h4>}
          {meaning.simple_explanation && <p>{meaning.simple_explanation}</p>}
          {meaning.cached && <p className="service-note">已复用这条语境释义。</p>}
          {meaning.warning && <p className="service-note">{meaning.warning}</p>}
        </>}
        {!meaning && !loading && !error && <><p className="service-note">上方是通用词义；需要时，可结合这次阅读的原句进一步讲解。</p><button className="text-button" disabled={disabled} onClick={() => void loadMeaning()}><RefreshCw size={14} />讲解这个词在原句中的意思</button></>}
      </div>}
      {occurrence && <div className="review-context"><p className="section-label">原文 · {occurrence.document_title}</p><blockquote lang="en">{occurrence.sentence || '该词暂无可用的原句，可回到原文查看。'}</blockquote><button className="text-button" disabled={disabled} onClick={() => onOpenDocument(occurrence.document_id, occurrence.id)}><BookOpen size={15} />回到原文 <ArrowRight size={14} /></button></div>}
    </section>}
  </div>;
}
