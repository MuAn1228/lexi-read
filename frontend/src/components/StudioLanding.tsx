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
  const pathsId = useId();

  return <div className="studio-landing">
    <ScrollSpread><section className="studio-landing__import" aria-label="导入阅读材料">
      <div className="studio-landing__upload">{upload}</div>
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
