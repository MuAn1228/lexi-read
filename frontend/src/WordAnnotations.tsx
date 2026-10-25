import type { ReactNode } from 'react';
import { Check, NotebookPen, Sparkles } from 'lucide-react';
import { hasMaimemoAnnotations } from './dictionary';
import type { DictionaryDetail, DictionaryResult, MaimemoAnnotations as MaimemoData } from './dictionary';
import './dictionary.css';

/** Shared, read-only word content. The owner decides when to fetch or generate. */
export default function WordAnnotations({ result, compact = false, showPhonetic = false, annotationAction, detailAction, afterDefinitions }: {
  result: DictionaryResult; compact?: boolean; showPhonetic?: boolean;
  annotationAction?: ReactNode; detailAction?: ReactNode; afterDefinitions?: ReactNode;
}) {
  return <div className={`word-annotations${compact ? ' word-annotations--compact' : ''}`}>
    {(result.maimemo || annotationAction) && <MaimemoAnnotations annotations={result.maimemo} action={annotationAction} />}
    <DictionaryBasics entry={result.dictionary} showPhonetic={showPhonetic} />
    {afterDefinitions}
    {(result.detail || detailAction) && <section className="dictionary-section dictionary-ai" aria-label="详细讲解">
      <div className="dictionary-section-heading"><h3><Sparkles size={17} />详细讲解</h3>{result.detail && <span className="dictionary-badge dictionary-badge--ai">AI 生成{result.cached ? ' · 已缓存' : ''}</span>}</div>
      {result.detail ? <DictionaryLesson detail={result.detail} /> : detailAction}
    </section>}
    {result.warning && <p className="dictionary-warning" role="status">{result.warning}</p>}
  </div>;
}

export function DictionaryBasics({ entry, showPhonetic = false }: { entry: DictionaryResult['dictionary']; showPhonetic?: boolean }) {
  return <section className="dictionary-section dictionary-offline" aria-label="通用中文释义">
    <div className="dictionary-section-heading"><h3>中文释义</h3>{entry && <span className="dictionary-badge">{entry.source === 'lexi_supplement' ? '词间补充注解' : '离线词典 · ECDICT'}</span>}</div>
    {entry ? <>
      {showPhonetic && (entry.phonetic || entry.headword) && <div className="annotation-word-meta"><span lang="en">{entry.headword}</span>{entry.phonetic && <span className="dictionary-phonetic">/{entry.phonetic.replace(/^\/+|\/+$/g, '')}/</span>}</div>}
      {entry.chinese.length > 0 ? <ol className="dictionary-definitions">{entry.chinese.map((meaning, index) => <li key={index}>{meaning}</li>)}</ol> : <p className="dictionary-empty">这个离线条目暂缺中文释义，可参考下方英文释义，或补充详细讲解。</p>}
      {entry.forms.length > 0 && <div className="dictionary-forms" aria-label="词形变化">{entry.forms.map((form, index) => <span key={index}><small>{form.label}</small><span lang="en">{form.word}</span></span>)}</div>}
      {entry.english.length > 0 && <details className="dictionary-english"><summary>展开英文释义（{entry.english.length}）</summary><ol lang="en">{entry.english.map((meaning, index) => <li key={index}>{meaning}</li>)}</ol></details>}
      {entry.examples?.map((example, index) => <blockquote className="dictionary-example" key={index}><p lang="en">{example.english}</p><p>{example.chinese}</p></blockquote>)}
      {Boolean(entry.usage_notes?.length) && <ul className="dictionary-usage">{entry.usage_notes!.map((note, index) => <li key={index}><Check size={14} /><span>{note}</span></li>)}</ul>}
      <a className="dictionary-source-link" href={entry.source_url} target="_blank" rel="noreferrer">{entry.source === 'lexi_supplement' ? '注解参考与拼写说明 ↗' : '词典来源与说明 ↗'}</a>
    </> : <p className="dictionary-empty">离线词典暂未收录这个词。可以查看墨墨个人注解，或尝试生成 AI 详细讲解。</p>}
  </section>;
}

export function MaimemoAnnotations({ annotations, action }: { annotations: MaimemoData | null; action?: ReactNode }) {
  const hasContent = hasMaimemoAnnotations(annotations);
  if (!hasContent) return <div className="annotation-source-status" aria-label="墨墨注解状态">
    {annotations && <p role="status">{annotations.warning || (annotations.status === 'unavailable' ? '暂时无法读取墨墨个人注解，其他释义仍可查看。' : annotations.status === 'not_found' ? '墨墨词汇查询暂未找到这个词，已使用其他词典。' : '墨墨中没有你自己创建的注解，以下使用本地词典；这不代表墨墨内置词典没有释义。')}</p>}
    {action}
  </div>;
  return <section className="dictionary-section dictionary-maimemo" aria-label="墨墨个人注解">
    <div className="dictionary-section-heading"><h3><NotebookPen size={17} />墨墨个人注解</h3><span className="dictionary-badge dictionary-badge--maimemo">自己创建{annotations?.cached ? ' · 已缓存' : ''}</span></div>
    <p className="annotation-source-note">优先展示你在墨墨中创建的释义、例句和助记。不是墨墨内置词典的完整内容。</p>
    {annotations!.interpretations.length > 0 && <div className="annotation-personal-group"><h4>我的释义</h4><ul className="annotation-personal-list">{annotations!.interpretations.map((item, index) => <li key={`${item.id}:${index}`}><p>{item.text}</p>{item.tags.length > 0 && <div className="annotation-tags">{item.tags.map((tag, tagIndex) => <span key={`${tag}:${tagIndex}`}>{tag}</span>)}</div>}</li>)}</ul></div>}
    {annotations!.phrases.length > 0 && <div className="annotation-personal-group"><h4>我的例句</h4>{annotations!.phrases.map((item, index) => <blockquote className="dictionary-example" key={`${item.id}:${index}`}><p lang="en">{item.english}</p>{item.chinese && <p>{item.chinese}</p>}{item.origin && <cite>{item.origin}</cite>}</blockquote>)}</div>}
    {annotations!.notes.length > 0 && <div className="annotation-personal-group"><h4>我的助记</h4><ul className="annotation-personal-list">{annotations!.notes.map((item, index) => <li key={`${item.id}:${index}`}><p>{item.text}</p></li>)}</ul></div>}
    {annotations!.warning && <p className="dictionary-warning" role="status">{annotations!.warning}</p>}
    {annotations!.stale && !annotations!.warning && <p className="annotation-source-note" role="status">正在使用上次保存的个人注解，可以重新读取更新。</p>}
    {action}
    <a className="dictionary-source-link" href="https://open.maimemo.com/" target="_blank" rel="noreferrer">墨墨开放 API 说明 ↗</a>
  </section>;
}

export function DictionaryLesson({ detail }: { detail: DictionaryDetail }) {
  return <>
    <p className="dictionary-summary">{detail.summary}</p>
    <div className="dictionary-senses">{detail.senses.map((sense, index) => <article className="dictionary-sense" key={index}><div className="dictionary-sense-heading"><span className="dictionary-sense-number">{String(index + 1).padStart(2, '0')}</span><div><span className="dictionary-pos">{sense.part_of_speech}</span><h4>{sense.meaning}</h4></div></div><p className="dictionary-explanation">{sense.explanation}</p>{sense.examples.map((example, exampleIndex) => <blockquote className="dictionary-example" key={exampleIndex}><p lang="en">{example.english}</p><p>{example.chinese}</p></blockquote>)}</article>)}</div>
    {detail.collocations.length > 0 && <div className="dictionary-detail-group"><h4>常用搭配</h4><div className="dictionary-collocations">{detail.collocations.map((item, index) => <article key={index}><strong lang="en">{item.phrase}</strong><span>{item.meaning}</span><p lang="en">{item.example}</p><p>{item.translation}</p></article>)}</div></div>}
    {detail.usage_notes.length > 0 && <div className="dictionary-detail-group"><h4>用法提示</h4><ul className="dictionary-usage">{detail.usage_notes.map((note, index) => <li key={index}><Check size={14} /><span>{note}</span></li>)}</ul></div>}
    {detail.confusables.length > 0 && <div className="dictionary-detail-group"><h4>易混辨析</h4><dl className="dictionary-confusables">{detail.confusables.map((item, index) => <div key={index}><dt lang="en">{item.word}</dt><dd>{item.distinction}</dd></div>)}</dl></div>}
    <p className="dictionary-ai-note">例句与讲解由 AI 生成，具体用法请结合原文判断。</p>
  </>;
}
