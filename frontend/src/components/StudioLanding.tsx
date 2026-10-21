import { useId } from 'react';
import type { ReactNode } from 'react';
import { ArrowRight } from 'lucide-react';
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
    <section className="studio-landing__import" aria-labelledby={importId}>
      <div className="studio-landing__intro">
        <p className="studio-landing__eyebrow">READ. COLLECT. REMEMBER.</p>
        <h2 id={importId}>读进文章，<br />留在<span>词间。</span></h2>
        <p className="studio-landing__description">从你想读的英文开始。查清一个词，读懂一句话，<br className="studio-landing__desktop-break" />让词汇在自己的阅读里慢慢积累。</p>
        <ol className="studio-landing__steps" aria-label="学习流程">
          <li><span aria-hidden="true">01</span><div><strong>导入文章</strong><span>图片、PDF、文本或网页</span></div></li>
          <li><span aria-hidden="true">02</span><div><strong>阅读查词</strong><span>结合原文理解中文释义</span></div></li>
          <li><span aria-hidden="true">03</span><div><strong>复习生词</strong><span>按学习节奏，再见一面</span></div></li>
        </ol>
      </div>
      <div className="studio-landing__upload"><div className="studio-landing__upload-caption"><span>新的阅读</span><span aria-hidden="true">＋</span></div>{upload}<p className="studio-landing__upload-note">材料保存到你的学习空间，随时回来接着读。</p></div>
    </section>

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
