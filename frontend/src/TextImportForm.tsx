import { ArrowRight, FileText } from 'lucide-react';
import { characterCount, normalizeArticle, TEXT_IMPORT_LIMIT, textImportProblem } from './textArticle';
import type { TextDraft } from './textArticle';

export default function TextImportForm({ draft, onChange, onSubmit, disabled }: {
  draft: TextDraft; onChange: (draft: TextDraft) => void; onSubmit: () => void; disabled: boolean;
}) {
  const count = characterCount(normalizeArticle(draft.text));
  const problem = textImportProblem(draft);
  return <form className="text-import-form" onSubmit={event => { event.preventDefault(); onSubmit(); }}>
    <p className="text-import-intro">带入你想读的英文，点击文章中的单词，记录自己的掌握程度。</p>
    <label htmlFor="text-import-title">文章标题 <span>选填</span></label>
    <input id="text-import-title" type="text" value={draft.title} disabled={disabled} placeholder="留空时使用文章第一行" onChange={event => onChange({ ...draft, title: event.target.value })} />
    <div className="text-import-label"><label htmlFor="text-import-content">英文文章</label><span className={count > TEXT_IMPORT_LIMIT ? 'over-limit' : ''} id="text-import-count">{count.toLocaleString('zh-CN')} / 20,000 字</span></div>
    <textarea id="text-import-content" autoFocus data-autofocus value={draft.text} disabled={disabled} rows={11} placeholder={'在这里粘贴英文文章…\n\n保留段落、标点与换行。'} aria-describedby="text-import-count text-import-hint" onChange={event => onChange({ ...draft, text: event.target.value })} />
    <p id="text-import-hint" className="text-import-hint">直接导入文字，无需上传图片。导入后即可查看语境释义、标记单词并加入复习。</p>
    {draft.text.length > 0 && problem && <p className="inline-error" role="alert">{problem}</p>}
    <button className="button primary text-import-submit" type="submit" disabled={disabled || Boolean(problem)}><FileText size={16} />导入并开始阅读<ArrowRight size={15} /></button>
  </form>;
}
