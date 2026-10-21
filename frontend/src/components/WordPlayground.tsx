import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent, PointerEvent } from 'react';
import { ArrowLeft, ArrowRight, BookOpen, RotateCw, Shuffle, Sparkles } from 'lucide-react';
import type { ReadingDocument, Vocabulary } from '../types';
import { curatedCards, personalInspirationCards, pickInspirationBatch } from '../wordInspiration';
import type { InspirationCard } from '../wordInspiration';
import './WordPlayground.css';

type WordPlaygroundProps = { disabled?: boolean; vocabulary?: Vocabulary[]; document?: ReadingDocument | null };
type Source = 'curated' | 'personal';
type Phase = 'idle' | 'flipping' | 'leaving' | 'entering';
type Deck = { source: Source; cards: InspirationCard[]; index: number; flipped: boolean };
const emptyVocabulary: Vocabulary[] = [];

/** An optional, read-only deck: personal cards keep their real reading context. */
export default function WordPlayground({ disabled = false, vocabulary = emptyVocabulary, document: readingDocument = null }: WordPlaygroundProps) {
  const titleId = useId();
  const helpId = useId();
  const personalPool = useMemo(() => personalInspirationCards(vocabulary, readingDocument), [vocabulary, readingDocument]);
  const personalPoolRef = useRef(personalPool);
  personalPoolRef.current = personalPool;
  const [deck, setDeck] = useState<Deck>(() => ({ source: 'curated', cards: pickInspirationBatch(curatedCards), index: 0, flipped: false }));
  const deckRef = useRef(deck);
  const [phase, setPhase] = useState<Phase>('idle');
  const phaseRef = useRef<Phase>('idle');
  const pending = useRef<Deck | null>(null);
  const timer = useRef<number | null>(null);
  const finishRef = useRef<() => void>(() => {});
  const settleRef = useRef<() => void>(() => {});
  const [announcement, setAnnouncement] = useState('');
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const reducedRef = useRef(reducedMotion);
  const finePointer = useRef(false);
  const tilt = useRef<HTMLDivElement>(null);
  const tiltFrame = useRef<number | null>(null);
  const pointer = useRef({ x: 0, y: 0 });
  const backContent = useRef<HTMLSpanElement>(null);
  const sample = deck.cards[deck.index];
  const busy = phase !== 'idle';
  const colors = { '--word-accent': sample?.accent, '--word-glow': sample?.glow } as CSSProperties;

  const commitDeck = (next: Deck) => { deckRef.current = next; setDeck(next); };
  const changePhase = (next: Phase) => { phaseRef.current = next; setPhase(next); };
  const clearTimer = () => { if (timer.current !== null) window.clearTimeout(timer.current); timer.current = null; };
  const clearTilt = () => {
    if (tiltFrame.current !== null) window.cancelAnimationFrame(tiltFrame.current);
    tiltFrame.current = null;
    tilt.current?.style.removeProperty('--word-tilt-x');
    tilt.current?.style.removeProperty('--word-tilt-y');
  };
  const armTimer = (milliseconds: number) => {
    clearTimer();
    timer.current = window.setTimeout(() => finishRef.current(), milliseconds);
  };
  // Freeze the visible data throughout a transition, then reconcile actual word
  // or article changes without reshuffling cards that are still available.
  const reconcilePersonal = () => {
    const current = deckRef.current;
    if (current.source !== 'personal' || phaseRef.current !== 'idle') return;
    const pool = personalPoolRef.current;
    const byId = new Map(pool.map(card => [card.id, card]));
    let cards = current.cards.flatMap(card => byId.has(card.id) ? [byId.get(card.id)!] : []);
    if (!cards.length && pool.length) cards = pickInspirationBatch(pool);
    if (JSON.stringify(cards) === JSON.stringify(current.cards)) return;
    const activeId = current.cards[current.index]?.id;
    const activeIndex = cards.findIndex(card => card.id === activeId);
    commitDeck({ ...current, cards, index: Math.max(0, activeIndex), flipped: activeIndex >= 0 && current.flipped });
  };
  const finish = () => {
    clearTimer();
    if (phaseRef.current === 'leaving' && pending.current) {
      const next = pending.current;
      pending.current = null;
      commitDeck(next);
      if (backContent.current) backContent.current.scrollTop = 0;
      changePhase('entering');
      armTimer(240);
      return;
    }
    changePhase('idle');
    reconcilePersonal();
  };
  finishRef.current = finish;
  const settle = () => {
    clearTimer(); clearTilt();
    if (pending.current) { commitDeck(pending.current); pending.current = null; }
    changePhase('idle');
    reconcilePersonal();
  };
  settleRef.current = settle;

  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
    const fine = window.matchMedia('(hover: hover) and (pointer: fine)');
    const preference = () => {
      reducedRef.current = reduce.matches;
      finePointer.current = fine.matches;
      setReducedMotion(reduce.matches);
      clearTilt();
      if (reduce.matches) settleRef.current();
    };
    const hidden = () => { if (window.document.hidden) settleRef.current(); };
    preference();
    reduce.addEventListener('change', preference);
    fine.addEventListener('change', preference);
    window.document.addEventListener('visibilitychange', hidden);
    window.addEventListener('blur', clearTilt);
    window.addEventListener('resize', clearTilt);
    window.addEventListener('scroll', clearTilt, true);
    return () => {
      clearTimer(); clearTilt(); pending.current = null;
      reduce.removeEventListener('change', preference);
      fine.removeEventListener('change', preference);
      window.document.removeEventListener('visibilitychange', hidden);
      window.removeEventListener('blur', clearTilt);
      window.removeEventListener('resize', clearTilt);
      window.removeEventListener('scroll', clearTilt, true);
    };
  }, []);
  useEffect(() => { if (disabled) settleRef.current(); }, [disabled]);
  useEffect(() => { if (phaseRef.current === 'idle') reconcilePersonal(); }, [personalPool]);

  const changeDeck = (next: Deck, message: string) => {
    if (disabled || phaseRef.current !== 'idle') return;
    clearTilt();
    setAnnouncement(message);
    if (reducedRef.current) { commitDeck(next); return; }
    pending.current = next;
    changePhase('leaving');
    armTimer(200);
  };
  const chooseWord = (nextIndex: number) => {
    const current = deckRef.current;
    if (!current.cards.length) return;
    const next = (nextIndex + current.cards.length) % current.cards.length;
    if (next === current.index) return;
    changeDeck({ ...current, index: next, flipped: false }, `第 ${next + 1} 张，${current.cards[next].word}。可以翻转查看。`);
  };
  const chooseSource = (source: Source) => {
    const current = deckRef.current;
    if (source === current.source) return;
    const cards = pickInspirationBatch(source === 'curated' ? curatedCards : personalPoolRef.current);
    changeDeck({ source, cards, index: 0, flipped: false }, cards.length
      ? `已切换到${source === 'curated' ? '精选词汇' : '我的词库'}，本组 ${cards.length} 张。`
      : '我的词库还没有可探索的词，回到阅读页标记后就会出现在这里。');
  };
  const refresh = () => {
    const current = deckRef.current;
    const pool = current.source === 'curated' ? curatedCards : personalPoolRef.current;
    if (!pool.length) return;
    const cards = pickInspirationBatch(pool, current.cards.map(card => card.id));
    changeDeck({ ...current, cards, index: 0, flipped: false }, `已换一组，本组 ${cards.length} 张。第一张是 ${cards[0].word}。`);
  };
  const flipCard = () => {
    const current = deckRef.current;
    const card = current.cards[current.index];
    if (disabled || phaseRef.current !== 'idle' || !card) return;
    clearTilt();
    commitDeck({ ...current, flipped: !current.flipped });
    setAnnouncement(current.flipped ? `已回到 ${card.word} 的单词面。` : `已揭晓 ${card.word} 的${card.meaning ? '释义和例句' : '阅读线索'}。`);
    if (!reducedRef.current) { changePhase('flipping'); armTimer(560); }
  };
  const moveTilt = (event: PointerEvent<HTMLDivElement>) => {
    if (disabled || phaseRef.current !== 'idle' || reducedRef.current || !finePointer.current || event.pointerType !== 'mouse') { clearTilt(); return; }
    pointer.current = { x: event.clientX, y: event.clientY };
    if (tiltFrame.current !== null) return;
    tiltFrame.current = window.requestAnimationFrame(() => {
      tiltFrame.current = null;
      const element = tilt.current;
      if (!element || phaseRef.current !== 'idle' || reducedRef.current) return;
      const bounds = element.getBoundingClientRect();
      const x = Math.max(-1, Math.min(1, ((pointer.current.x - bounds.left) / bounds.width - .5) * 2));
      const y = Math.max(-1, Math.min(1, ((pointer.current.y - bounds.top) / bounds.height - .5) * 2));
      element.style.setProperty('--word-tilt-x', `${(-y * 3).toFixed(2)}deg`);
      element.style.setProperty('--word-tilt-y', `${(x * 3).toFixed(2)}deg`);
    });
  };
  const scrollCard = (event: KeyboardEvent<HTMLButtonElement>) => {
    clearTilt();
    if (!deckRef.current.flipped || !backContent.current) return;
    const content = backContent.current;
    if (!['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Home') content.scrollTop = 0;
    else if (event.key === 'End') content.scrollTop = content.scrollHeight;
    else content.scrollTop += (event.key.includes('Down') ? 1 : -1) * (event.key.startsWith('Page') ? content.clientHeight * .8 : 36);
  };
  const unavailable = disabled || busy;
  const poolCount = deck.source === 'curated' ? curatedCards.length : personalPool.length;

  return <section className="word-playground" style={colors} aria-labelledby={titleId}
    data-disabled={disabled || undefined} data-phase={phase} data-reduced-motion={reducedMotion || undefined} data-source={deck.source}>
    <div className="word-playground__glow word-playground__glow--one" aria-hidden="true" />
    <div className="word-playground__glow word-playground__glow--two" aria-hidden="true" />
    <div className="word-playground__grid" aria-hidden="true" />
    <header className="word-playground__heading">
      <div><span className="word-playground__eyebrow"><Sparkles size={12} aria-hidden="true" /> A LITTLE WORD PLAY</span>
        <h2 id={titleId}>灵感词卡<span>随手翻一张</span></h2></div>
      <span className="word-playground__sample-label">{poolCount} 个{deck.source === 'curated' ? '精选词' : '词库词'}</span>
    </header>
    <div className="word-playground__toolbar">
      <div className="word-playground__sources" role="group" aria-label="词卡来源">
        <button type="button" onClick={() => chooseSource('curated')} disabled={disabled} aria-disabled={unavailable || undefined} aria-pressed={deck.source === 'curated'}>精选词汇</button>
        <button type="button" onClick={() => chooseSource('personal')} disabled={disabled} aria-disabled={unavailable || undefined} aria-pressed={deck.source === 'personal'}>我的词库</button>
      </div>
      <button className="word-playground__refresh" type="button" onClick={refresh} disabled={disabled || !poolCount}
        aria-disabled={unavailable || undefined} aria-label="换一组词卡"><Shuffle size={13} aria-hidden="true" />换一组</button>
    </div>
    <p id={helpId} className="word-playground__source-note">{deck.source === 'curated'
      ? '每组随机 6 张，换一组遇见更多词。'
      : '优先不认识和不确定的词；有当前文章原句时一同呈现。'}</p>

    <div className="word-playground__stage">
      <span className="word-playground__star word-playground__star--one" aria-hidden="true">✳</span>
      <span className="word-playground__star word-playground__star--two" aria-hidden="true">✧</span>
      {sample ? <div className="word-playground__tilt" ref={tilt} onPointerMove={moveTilt} onPointerLeave={clearTilt} onPointerDown={clearTilt}>
        <span className="word-playground__stack word-playground__stack--back" aria-hidden="true" />
        <span className="word-playground__stack word-playground__stack--middle" aria-hidden="true" />
        <div className="word-playground__scene" onTransitionEnd={event => {
          if (event.target === event.currentTarget && event.propertyName === 'opacity' && (phaseRef.current === 'leaving' || phaseRef.current === 'entering')) finishRef.current();
        }}>
          <button className="word-playground__card" type="button" onClick={flipCard} onKeyDown={scrollCard}
            disabled={disabled} aria-disabled={unavailable || undefined} aria-pressed={deck.flipped} aria-describedby={helpId}
            aria-label={deck.flipped
              ? `${sample.word}，${sample.meaning ?? '回到原句，认识这个词'}。${sample.sourceLabel}。${sample.contextLabel ?? ''}。${sample.sentence ?? '当前没有可用原句。'} 点击回到单词面。上下方向键可滚动原句。`
              : `翻转词卡 ${sample.word}，查看${sample.meaning ? '释义和例句' : '阅读线索'}。`}
          >
            <span className="word-playground__rotor" data-flipped={deck.flipped} onTransitionEnd={event => {
              if (event.target === event.currentTarget && event.propertyName === 'transform' && phaseRef.current === 'flipping') finishRef.current();
            }}>
              <span className="word-playground__face word-playground__front" aria-hidden="true">
                <span className="word-playground__card-kicker">{deck.source === 'curated' ? 'COLLECT A LITTLE WONDER' : 'FROM YOUR READING'}</span>
                <span className="word-playground__word" data-long={sample.word.length > 18 || undefined}>{sample.word}</span>
                <span className="word-playground__kind">{sample.kind}</span>
                <span className="word-playground__hint"><RotateCw size={12} /> 轻点卡片，发现另一面</span>
                <svg className="word-playground__sprout" viewBox="0 0 80 96" fill="none">
                  <path d="M39 88C44 65 38 43 47 13M41 65C24 64 13 55 13 38C32 38 42 47 41 65ZM42 47C58 45 69 34 68 20C51 23 42 32 42 47ZM45 28C30 24 26 14 29 5C43 8 48 17 45 28Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                  <path d="M18 87C29 84 49 84 61 88M23 93C34 91 44 91 54 94" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
                </svg>
                <span className="word-playground__card-number">{String(deck.index + 1).padStart(2, '0')}</span>
              </span>
              <span className="word-playground__face word-playground__back" aria-hidden="true">
                <span className="word-playground__back-content" ref={backContent}>
                  <span className="word-playground__back-word">{sample.word}<span>{sample.kind}</span></span>
                  <span className="word-playground__context">{sample.sourceLabel}{sample.contextLabel ? ` · ${sample.contextLabel}` : ''}</span>
                  <span className="word-playground__meaning" data-missing={!sample.meaning || undefined}>{sample.meaning ?? '回到原句，认识这个词'}</span>
                  <span className="word-playground__sentence" data-missing={!sample.sentence || undefined}>{sample.sentence ?? '当前没有可用原句。回到阅读页，结合上下文探索它的用法。'}</span>
                </span>
                <span className="word-playground__back-hint"><RotateCw size={12} /> 轻点翻回 · 原句较长时可滚动</span>
                <span className="word-playground__back-mark">✳</span>
              </span>
            </span>
          </button>
        </div>
      </div> : <div className="word-playground__empty"><BookOpen size={29} aria-hidden="true" /><strong>词库的灵感，等你来积累</strong><span>回到阅读页标记词汇后，就能在这里翻看。<br />也可以先切回精选词汇，遇见新词。</span></div>}
    </div>
    <div className="word-playground__controls">
      <button className="word-playground__step" type="button" onClick={() => chooseWord(deckRef.current.index - 1)} disabled={disabled || deck.cards.length < 2}
        aria-disabled={unavailable || undefined} aria-label="上一张词卡"><ArrowLeft size={15} aria-hidden="true" /></button>
      <div className="word-playground__pagination">
        <div className="word-playground__dots" role="group" aria-label="选择词卡">
          {deck.cards.map((entry, cardIndex) => <button key={cardIndex} className="word-playground__dot" type="button" disabled={disabled}
            aria-disabled={unavailable || undefined} onClick={() => chooseWord(cardIndex)} aria-label={`第 ${cardIndex + 1} 张：${entry.word}`} aria-pressed={cardIndex === deck.index}><span aria-hidden="true" /></button>)}
        </div>
        <span className="word-playground__count" aria-hidden="true">{deck.cards.length ? deck.index + 1 : 0}<span>/</span>{deck.cards.length}</span>
      </div>
      <button className="word-playground__step word-playground__step--next" type="button" onClick={() => chooseWord(deckRef.current.index + 1)} disabled={disabled || deck.cards.length < 2}
        aria-disabled={unavailable || undefined} aria-label="下一张词卡"><span>换一张</span><ArrowRight size={15} aria-hidden="true" /></button>
    </div>
    <span className="word-playground__announcement" role="status" aria-live="polite" aria-atomic="true">{announcement}</span>
  </section>;
}
