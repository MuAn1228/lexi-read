import { useId } from 'react';
import type { ReactNode } from 'react';
import { ArrowRight } from 'lucide-react';
import ScrollSpread from './ScrollSpread';
import './StudioLanding.css';

type StudioLandingProps = {
  upload: ReactNode;
  onNavigate: (view: 'library' | 'vocabulary' | 'review') => void;
  disabled: boolean;
};

const destinations = [
  { view: 'library', label: '文章库', description: '接着上次读到的地方。' },
  { view: 'vocabulary', label: '我的词库', description: '让每个生词有迹可循。' },
  { view: 'review', label: '生词复习', description: '给读过的单词再一次相遇。' },
] as const;

/** Import remains in the supplied slot; other actions open existing learning views. */
export default function StudioLanding({ upload, onNavigate, disabled }: StudioLandingProps) {
  const importId = useId();
  const pathsId = useId();

  return <div className="studio-landing">
    <ScrollSpread><section className="studio-landing__import" aria-labelledby={importId}>
      <div className="studio-landing__intro">
        <p className="studio-landing__eyebrow">A SPACE BETWEEN WORDS</p>
        <h2 id={importId}>读进去。<br /><span>记下来。</span></h2>
        <p className="studio-landing__description">读你感兴趣的英文，<br />把陌生的单词，变成自己的表达。</p>
        <ol className="studio-landing__steps" aria-label="学习流程">
          <li><span aria-hidden="true">01</span><div><strong>导入文章</strong><span>图片、PDF、文本或网页</span></div></li>
          <li><span aria-hidden="true">02</span><div><strong>阅读查词</strong><span>结合原文理解中文释义</span></div></li>
          <li><span aria-hidden="true">03</span><div><strong>复习生词</strong><span>按学习节奏，再见一面</span></div></li>
        </ol>
      </div>
      <div className="studio-landing__upload"><div className="studio-landing__upload-caption"><span>从一份英文材料开始</span><span aria-hidden="true">01 / READ</span></div>{upload}<p className="studio-landing__upload-note">图片、PDF、文本、网页，都可以成为下一篇阅读。</p></div>
    </section></ScrollSpread>

    <nav className="studio-landing__paths" aria-labelledby={pathsId}>
      <h2 id={pathsId}>继续学习</h2>
      <div className="studio-landing__tiles">
        {destinations.map(({ view, label, description }, index) => <button
          key={view}
          type="button"
          className="studio-landing__tile"
          disabled={disabled}
          onClick={() => onNavigate(view)}
        >
          <span className="studio-landing__tile-number" aria-hidden="true">0{index + 1}</span>
          <span className="studio-landing__tile-copy"><strong>{label}</strong><span>{description}</span></span>
          <ArrowRight className="studio-landing__tile-arrow" size={17} aria-hidden="true" />
        </button>)}
      </div>
    </nav>
  </div>;
}
