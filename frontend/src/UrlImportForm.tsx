import { ArrowRight, Globe, LoaderCircle } from 'lucide-react';
import { urlImportProblem } from './urlArticle';

export default function UrlImportForm({ url, onChange, onSubmit, disabled }: {
  url: string; onChange: (url: string) => void; onSubmit: () => void; disabled: boolean;
}) {
  const problem = urlImportProblem(url);
  return <form className="text-import-form url-import-form" noValidate onSubmit={event => { event.preventDefault(); if (!disabled && !problem) onSubmit(); }}>
    <p className="text-import-intro">粘贴感兴趣的英文文章链接，提取正文后，在这里继续阅读和学习。</p>
    <label htmlFor="url-import-address">英文网页链接</label>
    <input id="url-import-address" type="url" inputMode="url" autoFocus data-autofocus autoComplete="off" spellCheck={false} value={url} disabled={disabled} placeholder="https://example.com/article" aria-describedby="url-import-hint" onChange={event => onChange(event.target.value)} />
    <p id="url-import-hint" className="text-import-hint">支持公开网页，导入后保留标题、正文和原站链接。已导入的正文可直接在本机阅读。</p>
    <div className="url-import-note"><Globe size={17} /><p>暂不支持需要登录、付费或依赖脚本加载的文章。若网页无法提取，可切换到“粘贴文章”导入正文。</p></div>
    {url.length > 0 && problem && <p className="inline-error" role="alert">{problem}</p>}
    <button className="button primary text-import-submit" type="submit" disabled={disabled || Boolean(problem)}>{disabled ? <LoaderCircle size={16} className="spin" /> : <Globe size={16} />}导入网页并阅读<ArrowRight size={15} /></button>
  </form>;
}
