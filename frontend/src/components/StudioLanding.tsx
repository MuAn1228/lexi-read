import { useId } from 'react';
import type { ReactNode } from 'react';
import { ArrowRight, BookMarked, BookOpen, FolderOpen, RefreshCw } from 'lucide-react';
import './StudioLanding.css';

type StudioLandingProps = {
  upload: ReactNode;
  onNavigate: (view: 'library' | 'vocabulary' | 'review') => void;
  disabled: boolean;
};

const destinations = [
  { view: 'library', icon: FolderOpen, label: '文章库', description: '打开已保存的文章，继续阅读。' },
  { view: 'vocabulary', icon: BookMarked, label: '我的词库', description: '查看中文释义，管理收录的单词。' },
  { view: 'review', icon: RefreshCw, label: '生词复习', description: '复习到期生词，巩固薄弱词汇。' },
] as const;

/** Import remains in the supplied slot; other actions open existing learning views. */
export default function StudioLanding({ upload, onNavigate, disabled }: StudioLandingProps) {
  const importId = useId();
  const pathsId = useId();

  return <div className="studio-landing">
    <section className="studio-landing__import" aria-labelledby={importId}>
      <div className="studio-landing__import-heading">
        <span className="studio-landing__import-icon" aria-hidden="true"><BookOpen size={21} /></span>
        <div>
          <h2 id={importId}>导入阅读材料</h2>
          <p>选择图片、PDF、英文文本或网页链接，开始阅读和查词。</p>
        </div>
      </div>
      <ol className="studio-landing__steps" aria-label="学习流程">
        <li><span aria-hidden="true">1</span>导入文章</li>
        <li><span aria-hidden="true">2</span>阅读查词</li>
        <li><span aria-hidden="true">3</span>复习生词</li>
      </ol>
      <div className="studio-landing__upload">{upload}</div>
    </section>

    <nav className="studio-landing__paths" aria-labelledby={pathsId}>
      <h2 id={pathsId}>继续学习</h2>
      <div className="studio-landing__tiles">
        {destinations.map(({ view, icon: Icon, label, description }) => <button
          key={view}
          type="button"
          className="studio-landing__tile"
          disabled={disabled}
          onClick={() => onNavigate(view)}
        >
          <span className="studio-landing__tile-icon" aria-hidden="true"><Icon size={20} /></span>
          <span className="studio-landing__tile-copy"><strong>{label}</strong><span>{description}</span></span>
          <ArrowRight className="studio-landing__tile-arrow" size={17} aria-hidden="true" />
        </button>)}
      </div>
    </nav>
  </div>;
}
