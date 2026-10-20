import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, ArrowRight, Bookmark, BookOpen, FileText, LoaderCircle, Pencil, RefreshCw, Search, Tag, Trash2, Undo2, X } from 'lucide-react';
import { errorMessage, formatDate, request } from './api';
import { articleEdit, articleSource, libraryPath } from './documentLibrary';
import type { DocumentPatch } from './documentLibrary';
import type { DocumentSummary } from './types';

export default function DocumentLibraryView({ documents, revision, disabled, onOpen, onUpdate, onDelete, onRestore }: {
  documents: DocumentSummary[]; revision: number; disabled: boolean;
  onOpen: (id: number) => void;
  onUpdate: (id: number, patch: DocumentPatch) => Promise<DocumentSummary>;
  onDelete: (id: number) => Promise<DocumentSummary>;
  onRestore: (id: number) => Promise<DocumentSummary>;
}) {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [tag, setTag] = useState('');
  const [source, setSource] = useState('');
  const [sort, setSort] = useState('newest');
  const [deleted, setDeleted] = useState(false);
  const [items, setItems] = useState<DocumentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [reload, setReload] = useState(0);
  const [editing, setEditing] = useState<DocumentSummary | null>(null);
  const [removing, setRemoving] = useState<DocumentSummary | null>(null);
  const [title, setTitle] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [addedTags, setAddedTags] = useState('');
  const [mutationError, setMutationError] = useState('');
  const [pending, setPending] = useState(false);
  const modal = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const pendingRef = useRef(false);
  const modalOpen = Boolean(editing || removing);
  const generation = useRef(0);
  useEffect(() => { const timer = setTimeout(() => setSearch(query), 250); return () => clearTimeout(timer); }, [query]);
  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    setLoading(true); setLoadError('');
    void request<DocumentSummary[]>(libraryPath({ q: search, tag, source, sort, deleted }), { signal: controller.signal }).then(values => {
      if (current === generation.current && !controller.signal.aborted) setItems(values);
    }).catch(error => { if (!controller.signal.aborted && current === generation.current) setLoadError(errorMessage(error)); })
      .finally(() => { if (current === generation.current && !controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); ++generation.current; };
  }, [search, tag, source, sort, deleted, revision, reload]);
  const availableTags = useMemo(() => {
    const values = new Map<string, string>();
    [...documents, ...items].forEach(item => item.tags?.forEach(value => { if (!values.has(value.toLowerCase())) values.set(value.toLowerCase(), value); }));
    if (tag && !values.has(tag.toLowerCase())) values.set(tag.toLowerCase(), tag);
    return [...values.values()].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  }, [documents, items, tag]);
  useEffect(() => {
    if (!modalOpen || !modal.current) return;
    const element = modal.current;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const controls = () => Array.from(element.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]')).filter(control => control.getClientRects().length > 0);
    const first = () => (element.querySelector<HTMLElement>('[data-autofocus]') ?? controls()[0] ?? element).focus();
    first();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); if (!pendingRef.current) { setEditing(null); setRemoving(null); setMutationError(''); } }
      if (event.key !== 'Tab') return;
      const list = controls(); const start = list[0]; const end = list.at(-1);
      if (!start) { event.preventDefault(); element.focus(); }
      else if (event.shiftKey && (document.activeElement === start || document.activeElement === element)) { event.preventDefault(); end?.focus(); }
      else if (!event.shiftKey && (document.activeElement === end || document.activeElement === element)) { event.preventDefault(); start.focus(); }
    };
    const focus = (event: FocusEvent) => { if (!element.contains(event.target as Node)) first(); };
    document.addEventListener('keydown', key); document.addEventListener('focusin', focus);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', key); document.removeEventListener('focusin', focus);
      if (previousFocus?.isConnected && !previousFocus.matches(':disabled')) previousFocus.focus(); else heading.current?.focus();
    };
  }, [modalOpen]);
  const closeModal = () => { if (!pendingRef.current) { setEditing(null); setRemoving(null); setMutationError(''); } };
  const mutate = async (action: () => Promise<DocumentSummary>) => {
    if (pendingRef.current || disabled) return;
    pendingRef.current = true; setPending(true); setMutationError('');
    // Invalidate an outstanding list read before a successful mutation changes its snapshot.
    ++generation.current;
    try {
      await action(); setEditing(null); setRemoving(null);
      setReload(value => value + 1);
    } catch (error) { setMutationError(errorMessage(error)); setReload(value => value + 1); }
    finally { pendingRef.current = false; setPending(false); }
  };
  const openEditor = (item: DocumentSummary) => {
    setEditing(item); setTitle(item.title); setTags(item.tags ?? []); setAddedTags(''); setMutationError('');
  };
  const saveEdit = () => {
    if (!editing) return;
    const result = articleEdit(title, tags, addedTags);
    if (result.error || !result.patch) { setMutationError(result.error ?? '请检查文章信息。'); return; }
    void mutate(() => onUpdate(editing.id, result.patch!));
  };
  return <section className="document-library" aria-label="文章库管理">
    <div className="article-library-heading"><div><BookOpen size={21} /><h2 ref={heading} tabIndex={-1}>我的文章库</h2><span className="count-badge">{documents.length} 篇在读</span></div><div className="segmented" role="group" aria-label="文章库范围"><button className={!deleted ? 'active' : ''} aria-pressed={!deleted} disabled={disabled || pending} onClick={() => { setDeleted(false); setMutationError(''); }}>活跃文章</button><button className={deleted ? 'active' : ''} aria-pressed={deleted} disabled={disabled || pending} onClick={() => { setDeleted(true); setMutationError(''); }}><Trash2 size={14} />回收站</button></div></div>
    <div className="article-library-filters panel"><label className="word-search"><Search size={17} /><input aria-label="搜索文章" placeholder="搜索标题或正文…" value={query} onChange={event => setQuery(Array.from(event.target.value).slice(0, 200).join(''))} disabled={disabled || pending} /></label><label><span>标签</span><select aria-label="文章标签" value={tag} onChange={event => setTag(event.target.value)} disabled={disabled || pending}><option value="">全部标签</option>{availableTags.map(value => <option key={value} value={value}>{value}</option>)}</select></label><label><span>来源</span><select aria-label="文章来源" value={source} onChange={event => setSource(event.target.value)} disabled={disabled || pending}><option value="">全部来源</option><option value="text">文字文章</option><option value="upload">图片文章</option><option value="pdf">PDF 文档</option><option value="web">网页文章</option><option value="demo">示例文章</option></select></label><label><span>排序</span><select aria-label="文章排序" value={sort} onChange={event => setSort(event.target.value)} disabled={disabled || pending}><option value="newest">最新导入</option><option value="oldest">最早导入</option><option value="title">标题顺序</option></select></label></div>
    {deleted && <p className="article-library-note">回收站中的文章不参与阅读和原文跳转。恢复后，标签和书签仍然保留；词库和学习记录不受影响。</p>}
    {loadError && <div className="error-banner" role="alert"><AlertCircle size={18} /><span>文章列表暂未刷新：{loadError}</span><button className="text-button" disabled={loading || disabled} onClick={() => setReload(value => value + 1)}><RefreshCw size={14} />重新加载文章列表</button></div>}
    {!modalOpen && mutationError && <div className="error-banner" role="alert"><AlertCircle size={18} /><span>{mutationError}</span><button className="icon-button" aria-label="关闭文章操作错误" onClick={() => setMutationError('')}><X size={16} /></button></div>}
    <p className="article-library-result" role="status">{loading ? <><LoaderCircle size={14} className="spin" />正在整理文章…</> : loadError ? '可重新加载列表，已保存的操作无需重复提交。' : `找到 ${items.length} 篇${deleted ? '回收站' : ''}文章`}</p>
    {!items.length && !loading && !loadError ? <div className="panel article-library-empty"><FileText size={34} /><h3>{search || tag || source ? '暂时没有匹配的文章' : deleted ? '回收站是空的' : '从一篇感兴趣的文章开始'}</h3><p>{search || tag || source ? '换一个关键词，或清除筛选条件再看看。' : deleted ? '移到回收站的文章可以在这里恢复。' : '点击上方“添加阅读材料”，上传图片、PDF、粘贴文章或导入网页链接。'}</p>{(search || tag || source) && <button className="button secondary" onClick={() => { setQuery(''); setSearch(''); setTag(''); setSource(''); }}>清除筛选条件</button>}</div>
      : <div className={`document-grid ${loading ? 'is-loading' : ''}`} aria-busy={loading}>{items.map(item => <article key={item.id} className="document-card panel studio-spotlight" data-document-id={item.id}>
        <div className="article-card-meta"><span className="tiny-badge">{articleSource(item.source)}</span>{item.bookmark_occurrence_id !== null && <span className="article-bookmarked"><Bookmark size={13} />已存书签</span>}<span>{formatDate(item.created_at)}</span></div>
        <h3>{item.title}</h3><p className="article-excerpt">{item.excerpt || '打开文章，继续你的阅读。'}</p>
        <div className="article-tags" aria-label="文章标签列表">{item.tags?.length ? item.tags.map(value => <button key={value} disabled={disabled || pending} className="article-tag" onClick={() => setTag(value)}><Tag size={11} />{value}</button>) : <span className="article-no-tags">尚未添加标签</span>}</div>
        <div className="article-card-bottom"><span>{item.source === 'pdf' && `${item.page_count} 页 · `}{item.word_count} 处英文单词</span>{deleted ? <button className="button secondary" aria-label={`恢复 ${item.title}`} disabled={disabled || pending || loading} onClick={() => void mutate(() => onRestore(item.id))}><Undo2 size={15} />恢复文章</button> : <button className="text-button" aria-label={`阅读 ${item.title}`} disabled={disabled || pending || loading} onClick={() => onOpen(item.id)}>继续阅读<ArrowRight size={15} /></button>}</div>
        {!deleted && <div className="article-card-actions"><button className="text-button" aria-label={`编辑 ${item.title}`} disabled={disabled || pending || loading} onClick={() => openEditor(item)}><Pencil size={14} />标题与标签</button><button className="text-button article-delete" aria-label={`移到回收站 ${item.title}`} disabled={disabled || pending || loading} onClick={() => { setRemoving(item); setMutationError(''); }}><Trash2 size={14} />移到回收站</button></div>}
      </article>)}</div>}
    {modalOpen && <div className="modal-backdrop" onClick={closeModal}><section ref={modal} tabIndex={-1} role="dialog" aria-modal="true" aria-label={editing ? '编辑文章' : '移到回收站'} className="article-edit-modal panel" onClick={event => event.stopPropagation()}><div className="panel-heading"><div>{editing ? <Pencil size={17} /> : <Trash2 size={17} />}<h2>{editing ? '编辑文章' : '移到回收站'}</h2></div><button className="icon-button" aria-label={editing ? '关闭文章编辑' : '关闭回收站确认'} disabled={pending} onClick={closeModal}><X size={18} /></button></div>
      {editing ? <form onSubmit={event => { event.preventDefault(); saveEdit(); }} className="article-edit-form"><label>文章标题<input data-autofocus aria-label="文章标题" value={title} disabled={pending} onChange={event => setTitle(event.target.value)} /></label><div className="article-edit-tags"><span>已有标签</span><div className="article-tags">{tags.length ? tags.map(value => <button type="button" className="article-tag" key={value} disabled={pending} aria-label={`移除标签 ${value}`} onClick={() => setTags(items => items.filter(item => item !== value))}>{value}<X size={12} /></button>) : <p>还没有标签</p>}</div></div><label>添加标签<input aria-label="文章标签（逗号分隔）" value={addedTags} disabled={pending} placeholder="例如：科技, 考试, 兴趣阅读" onChange={event => setAddedTags(event.target.value)} /><small>用逗号分隔，每篇最多 10 个标签，每个最多 20 字。</small></label>{mutationError && <p className="modal-error" role="alert">{mutationError}</p>}<div className="article-modal-actions"><button type="button" className="button secondary" disabled={pending} onClick={closeModal}>取消</button><button type="submit" className="button primary" disabled={pending || disabled}>{pending && <LoaderCircle size={15} className="spin" />}保存文章信息</button></div></form>
        : <div className="article-delete-content"><h3>{removing?.title}</h3><p>移到回收站，词库和学习记录保留。文章会从阅读列表隐藏，之后可在回收站恢复。</p>{mutationError && <p className="modal-error" role="alert">{mutationError}</p>}<div className="article-modal-actions"><button data-autofocus className="button secondary" disabled={pending} onClick={closeModal}>取消</button><button className="button danger" disabled={pending || disabled} onClick={() => removing && void mutate(() => onDelete(removing.id))}>{pending && <LoaderCircle size={15} className="spin" />}确认移到回收站</button></div></div>}
    </section></div>}
  </section>;
}
