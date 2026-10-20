import { useId } from 'react';
import type { ReactNode } from 'react';
import { ArrowRight, ArrowUpRight, BookOpen, FolderOpen, Leaf, RefreshCw, Sparkles } from 'lucide-react';
import ReadingGarden from './ReadingGarden';
import './StudioLanding.css';

type StudioLandingProps = {
  upload: ReactNode;
  onNavigate: (view: 'library' | 'review' | 'practice') => void;
  disabled: boolean;
};

const destinations = [
  { view: 'library', icon: FolderOpen, label: '文章库', english: 'THE READING SHELF', description: '给好文章留个位置，下次接着读。', tone: 'sage' },
  { view: 'review', icon: RefreshCw, label: '生词复习', english: 'A LITTLE REVISIT', description: '回到熟悉的原句，再认识一次生词。', tone: 'apricot' },
  { view: 'practice', icon: Sparkles, label: '短文练习', english: 'YOUR NEXT STORY', description: '让自己的生词，出现在新的故事里。', tone: 'lavender' },
] as const;

/** The first reading desk. All import behavior stays in the supplied upload slot. */
export default function StudioLanding({ upload, onNavigate, disabled }: StudioLandingProps) {
  const titleId = useId();
  const importId = useId();

  return <div className="studio-landing">
    <div className="studio-landing__desk">
      <section className="studio-landing__hero studio-spotlight" aria-labelledby={titleId}>
        <div className="studio-landing__hero-topline">
          <span className="studio-landing__edition"><Leaf size={13} aria-hidden="true" /> A SPACE FOR CURIOSITY</span>
          <span className="studio-landing__sun" aria-hidden="true">✳</span>
        </div>
        <div className="studio-landing__hero-copy">
          <p className="studio-landing__counterline">Read a little. Grow a little.</p>
          <h2 id={titleId} className="studio-landing__title">把好奇心，<br />读成<span className="studio-landing__title-mark">自己的世界。
            <svg viewBox="0 0 300 16" preserveAspectRatio="none" aria-hidden="true"><path d="M3 11C76 0 173 1 294 8M17 14C107 5 191 5 283 11" /></svg>
          </span></h2>
          <p className="studio-landing__description">带上喜欢的文章，在真实的语境里，<br className="studio-landing__optional-break" />让每一个新词慢慢变成自己的表达。</p>
        </div>

        <div className="studio-landing__garden-desk">
          <div className="studio-landing__paper-stack" aria-hidden="true">
            <div className="studio-landing__paper-back" />
            <div className="studio-landing__word-note">
              <span className="studio-landing__note-tab" />
              <span className="studio-landing__note-label">A WORD TO BEGIN</span>
              <strong>curiosity</strong>
              <span className="studio-landing__note-meaning">n. 好奇心 · 求知欲</span>
              <span className="studio-landing__note-rule" />
              <span className="studio-landing__note-example">There is a story<br />in every word.</span>
              <Leaf size={16} className="studio-landing__note-leaf" />
            </div>
          </div>
          <div className="studio-landing__garden"><ReadingGarden /></div>
        </div>
        <div className="studio-landing__journey" aria-label="学习方式：带入文章，发现生词，连接新知">
          <span><i aria-hidden="true">01</i>带入文章</span><ArrowRight size={13} aria-hidden="true" />
          <span><i aria-hidden="true">02</i>发现生词</span><ArrowRight size={13} aria-hidden="true" />
          <span><i aria-hidden="true">03</i>连接新知</span>
        </div>
      </section>

      <section className="studio-landing__import studio-spotlight" aria-labelledby={importId}>
        <div className="studio-landing__import-heading">
          <div><p className="studio-landing__section-overline">OPEN A NEW CHAPTER</p><h2 id={importId}>今天，从哪一篇开始？</h2></div>
          <span className="studio-landing__import-symbol" aria-hidden="true"><BookOpen size={23} /></span>
        </div>
        <div className="studio-landing__upload">{upload}</div>
        <div className="studio-landing__import-footnote"><span className="studio-landing__footnote-line" aria-hidden="true" /><p>一篇喜欢的文章，就是一个很好的开始。</p><Leaf size={13} aria-hidden="true" /></div>
      </section>
    </div>

    <nav className="studio-landing__paths" aria-label="继续探索学习空间">
      <div className="studio-landing__paths-heading"><p>把阅读，接着往下走。</p><span>YOUR LITTLE LEARNING RITUALS</span></div>
      <div className="studio-landing__tiles">
        {destinations.map(({ view, icon: Icon, label, english, description, tone }) => <button
          key={view}
          type="button"
          className={`studio-landing__tile studio-landing__tile--${tone} studio-spotlight`}
          disabled={disabled}
          onClick={() => onNavigate(view)}
        >
          <span className="studio-landing__tile-top"><span className="studio-landing__tile-icon"><Icon size={20} aria-hidden="true" /></span><span className="studio-landing__tile-arrow"><ArrowUpRight size={19} aria-hidden="true" /></span></span>
          <span className="studio-landing__tile-body"><span className="studio-landing__tile-overline">{english}</span><strong>{label}</strong><span className="studio-landing__tile-description">{description}</span></span>
        </button>)}
      </div>
    </nav>
  </div>;
}
