import { useId, useState } from 'react';
import type { CSSProperties } from 'react';
import { ArrowLeft, ArrowRight, RotateCw, Sparkles } from 'lucide-react';
import './WordPlayground.css';

type WordPlaygroundProps = { disabled?: boolean };

// Editorial examples only. This little card deck never reads or writes learning data.
const sampleWords = [
  { word: 'curiosity', kind: 'noun', meaning: '好奇心 · 求知欲', sentence: 'Curiosity turns a quiet walk into a little adventure.', accent: '#d8eaa1', glow: '#b7dc76' },
  { word: 'explore', kind: 'verb', meaning: '探索 · 发现', sentence: 'Explore a new path and see where it leads.', accent: '#efc5a5', glow: '#d9a58e' },
  { word: 'wonder', kind: 'noun', meaning: '惊奇 · 好奇', sentence: 'The night sky fills us with wonder.', accent: '#d5c8f0', glow: '#b5a1e5' },
  { word: 'bloom', kind: 'verb', meaning: '绽放 · 成长', sentence: 'With a little care, small ideas begin to bloom.', accent: '#f0cfce', glow: '#d6a3b6' },
  { word: 'spark', kind: 'noun / verb', meaning: '火花 · 激发', sentence: 'One good question can spark a new idea.', accent: '#eddd9d', glow: '#dbc76b' },
  { word: 'serendipity', kind: 'noun', meaning: '意外发现的美好', sentence: 'Finding this little bookshop was pure serendipity.', accent: '#bce0d0', glow: '#8fc7b3' },
] as const;

/** A tactile, self-contained sample deck. There is no autoplay or learning mutation. */
export default function WordPlayground({ disabled = false }: WordPlaygroundProps) {
  const titleId = useId();
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const sample = sampleWords[index];
  const colors = { '--word-accent': sample.accent, '--word-glow': sample.glow } as CSSProperties;

  const chooseWord = (nextIndex: number) => {
    if (disabled) return;
    const next = (nextIndex + sampleWords.length) % sampleWords.length;
    setIndex(next);
    setFlipped(false);
    setAnnouncement(`第 ${next + 1} 张，${sampleWords[next].word}。可以翻转查看释义。`);
  };

  const flipCard = () => {
    if (disabled) return;
    const nextFlipped = !flipped;
    setFlipped(nextFlipped);
    setAnnouncement(nextFlipped ? `已揭晓 ${sample.word} 的释义。` : `已回到 ${sample.word} 的单词面。`);
  };

  return <section className="word-playground" style={colors} aria-labelledby={titleId} data-disabled={disabled || undefined}>
    <div className="word-playground__glow word-playground__glow--one" aria-hidden="true" />
    <div className="word-playground__glow word-playground__glow--two" aria-hidden="true" />
    <div className="word-playground__grid" aria-hidden="true" />
    <header className="word-playground__heading">
      <div>
        <span className="word-playground__eyebrow"><Sparkles size={12} aria-hidden="true" /> A LITTLE WORD PLAY</span>
        <h2 id={titleId}>灵感词卡<span>随手翻一张</span></h2>
      </div>
      <span className="word-playground__sample-label">精选示例</span>
    </header>

    <div className="word-playground__stage">
      <span className="word-playground__star word-playground__star--one" aria-hidden="true">✳</span>
      <span className="word-playground__star word-playground__star--two" aria-hidden="true">✧</span>
      <div className="word-playground__tilt" data-motion-tilt={!disabled ? '' : undefined}>
        <span className="word-playground__stack word-playground__stack--back" aria-hidden="true" />
        <span className="word-playground__stack word-playground__stack--middle" aria-hidden="true" />
        <button
          className="word-playground__card"
          type="button"
          onClick={flipCard}
          disabled={disabled}
          aria-pressed={flipped}
          aria-label={flipped
            ? `${sample.word}，${sample.meaning}。例句：${sample.sentence} 点击回到单词面。`
            : `翻转词卡 ${sample.word}，查看释义和例句。`}
          data-motion-burst=""
        >
          <span className="word-playground__rotor" data-flipped={flipped} key={sample.word}>
            <span className="word-playground__face word-playground__front" aria-hidden="true">
              <span className="word-playground__card-kicker">COLLECT A LITTLE WONDER</span>
              <span className="word-playground__word">{sample.word}</span>
              <span className="word-playground__kind">{sample.kind}</span>
              <span className="word-playground__hint"><RotateCw size={12} /> 轻点卡片，发现另一面</span>
              <svg className="word-playground__sprout" viewBox="0 0 80 96" fill="none">
                <path d="M39 88C44 65 38 43 47 13M41 65C24 64 13 55 13 38C32 38 42 47 41 65ZM42 47C58 45 69 34 68 20C51 23 42 32 42 47ZM45 28C30 24 26 14 29 5C43 8 48 17 45 28Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                <path d="M18 87C29 84 49 84 61 88M23 93C34 91 44 91 54 94" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
              </svg>
              <span className="word-playground__card-number">0{index + 1}</span>
            </span>
            <span className="word-playground__face word-playground__back" aria-hidden="true">
              <span className="word-playground__back-word">{sample.word}<span>{sample.kind}</span></span>
              <span className="word-playground__meaning">{sample.meaning}</span>
              <span className="word-playground__sentence">{sample.sentence}</span>
              <span className="word-playground__back-hint"><RotateCw size={12} /> 再点一下，回到单词</span>
              <span className="word-playground__back-mark">✳</span>
            </span>
          </span>
        </button>
      </div>
    </div>

    <div className="word-playground__controls">
      <button className="word-playground__step" type="button" onClick={() => chooseWord(index - 1)} disabled={disabled} aria-label="上一张精选词卡" data-motion-burst=""><ArrowLeft size={15} aria-hidden="true" /></button>
      <div className="word-playground__pagination">
        <div className="word-playground__dots" role="group" aria-label="选择精选词卡">
          {sampleWords.map((entry, cardIndex) => <button
            key={entry.word}
            className="word-playground__dot"
            type="button"
            disabled={disabled}
            onClick={() => chooseWord(cardIndex)}
            aria-label={`第 ${cardIndex + 1} 张：${entry.word}`}
            aria-pressed={cardIndex === index}
          ><span aria-hidden="true" /></button>)}
        </div>
        <span className="word-playground__count" aria-hidden="true">{index + 1}<span>/</span>{sampleWords.length}</span>
      </div>
      <button className="word-playground__step word-playground__step--next" type="button" onClick={() => chooseWord(index + 1)} disabled={disabled} aria-label="下一张精选词卡" data-motion-burst=""><span>换一张</span><ArrowRight size={15} aria-hidden="true" /></button>
    </div>
    <span className="word-playground__announcement" role="status" aria-live="polite" aria-atomic="true">{announcement}</span>
  </section>;
}
