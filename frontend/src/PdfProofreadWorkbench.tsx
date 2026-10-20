import { AlertCircle, Check, ChevronLeft, ChevronRight, Copy, FileCheck2, FileText, LoaderCircle, RotateCcw, Save, ScanLine, X, ZoomIn, ZoomOut } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ApiError, errorMessage, request, requestImage } from './api';
import { normalizeProofreadText, proofreadChangedPages, proofreadCharacterCount, proofreadPayload, proofreadProblem } from './pdfProofread';
import type { ProofreadDocument, ProofreadPage, ProofreadPayload } from './pdfProofread';
import type { ReadingDocument } from './types';
import './pdfProofread.css';

type Confirmation = 'close' | 'reset' | 'reload' | null;
export default function PdfProofreadWorkbench({ documentId, title, initialPage, opener, onClose, onSaved, onUncertainClose }: {
  documentId: number; title: string; initialPage: number; opener: HTMLElement | null;
  onClose: () => void; onSaved: (document: ReadingDocument) => void; onUncertainClose: () => void;
}) {
  const [source, setSource] = useState<ProofreadDocument | null>(null);
  const [pages, setPages] = useState<ProofreadPage[]>([]);
  const [pageNumber, setPageNumber] = useState(initialPage);
  const [zoom, setZoom] = useState(100);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [stale, setStale] = useState(false);
  const [attempt, setAttempt] = useState<ProofreadPayload | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [imageUrl, setImageUrl] = useState('');
  const [imageError, setImageError] = useState('');
  const [imageAttempt, setImageAttempt] = useState(0);
  const [copied, setCopied] = useState(false);
  const dialog = useRef<HTMLElement>(null);
  const confirmBox = useRef<HTMLDivElement>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const active = useRef(true);
  const pending = useRef(false);
  const needsReaderSync = useRef(false);
  const loadController = useRef<AbortController | null>(null);
  const saveController = useRef<AbortController | null>(null);
  const state = useRef({ busy: true, dirty: false, uncertain: false, confirmation: null as Confirmation });
  const callbacks = useRef({ onClose, onSaved, onUncertainClose });
  callbacks.current = { onClose, onSaved, onUncertainClose };
  const titleId = useId(); const descriptionId = useId(); const editorId = useId(); const confirmationId = useId();
  const changed = proofreadChangedPages(source?.pages ?? [], pages);
  const busy = loading || saving;
  const locked = busy || attempt !== null;
  const page = pages.find(item => item.number === pageNumber);
  const currentChanged = changed.includes(pageNumber);
  const characters = proofreadCharacterCount(pages);
  const validation = source ? proofreadProblem(source.pages, pages, source.max_characters) : '';
  state.current = { busy, dirty: changed.length > 0, uncertain: attempt !== null, confirmation };

  const load = async () => {
    if (pending.current) return;
    loadController.current?.abort();
    const controller = new AbortController(); loadController.current = controller;
    pending.current = true; setLoading(true); setError('');
    try {
      const value = await request<ProofreadDocument>(`/documents/${documentId}/proofread`, { signal: controller.signal });
      if (!active.current || controller.signal.aborted) return;
      setSource(value); setPages(value.pages.map(item => ({ ...item })));
      setPageNumber(number => value.pages.some(item => item.number === number) ? number : value.pages[0]?.number ?? 1);
      setAttempt(null); setStale(false); setConfirmation(null); setCopied(false);
    } catch (e) { if (active.current && !controller.signal.aborted) setError(errorMessage(e)); }
    finally { if (active.current && !controller.signal.aborted) { pending.current = false; setLoading(false); } }
  };
  useEffect(() => {
    active.current = true; pending.current = false;
    void load();
    return () => { active.current = false; loadController.current?.abort(); saveController.current?.abort(); };
  // The workbench is keyed by the document. Drafts are never refreshed in the background.
  }, [documentId]);

  useEffect(() => {
    if (!source) return;
    const controller = new AbortController(); let objectUrl: string | undefined; let current = true;
    setImageUrl(''); setImageError('');
    void requestImage(`/api/documents/${documentId}/pdf/pages/${pageNumber}/image`, controller.signal).then(blob => {
      if (!current) return;
      objectUrl = URL.createObjectURL(blob); setImageUrl(objectUrl);
    }).catch(e => { if (current && !controller.signal.aborted) setImageError(errorMessage(e)); });
    return () => { current = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [documentId, source, pageNumber, imageAttempt]);

  useEffect(() => {
    const dom = window.document; const modal = dialog.current;
    if (!modal) return;
    const previousOverflow = dom.body.style.overflow;
    const shell = dom.querySelector<HTMLElement>('.app-shell'); const previousInert = shell?.inert ?? false;
    dom.body.style.overflow = 'hidden'; if (shell) shell.inert = true;
    // The close button is disabled during the initial read; the dialog is always focusable.
    (closeButton.current?.disabled ? modal : closeButton.current ?? modal).focus();
    const focusable = () => Array.from((confirmBox.current ?? modal).querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
    const focusFirst = () => (focusable()[0] ?? modal).focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (pending.current || state.current.busy) return;
        if (state.current.confirmation) { setConfirmation(null); return; }
        if (state.current.dirty || state.current.uncertain) setConfirmation('close');
        else if (needsReaderSync.current) callbacks.current.onUncertainClose();
        else callbacks.current.onClose();
      }
      if (event.key !== 'Tab') return;
      const elements = focusable(); const first = elements[0]; const last = elements[elements.length - 1];
      if (!first) { event.preventDefault(); modal.focus(); }
      else if (event.shiftKey && (dom.activeElement === first || dom.activeElement === modal)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (dom.activeElement === last || dom.activeElement === modal)) { event.preventDefault(); first.focus(); }
    };
    const focus = (event: FocusEvent) => { if (!(confirmBox.current ?? modal).contains(event.target as Node)) focusFirst(); };
    const unload = (event: BeforeUnloadEvent) => { if (pending.current || state.current.dirty || state.current.uncertain || state.current.busy) { event.preventDefault(); event.returnValue = ''; } };
    dom.addEventListener('keydown', key, true); dom.addEventListener('focusin', focus); window.addEventListener('beforeunload', unload);
    return () => {
      dom.body.style.overflow = previousOverflow; if (shell) shell.inert = previousInert;
      dom.removeEventListener('keydown', key, true); dom.removeEventListener('focusin', focus); window.removeEventListener('beforeunload', unload);
      if (opener?.isConnected && !opener.matches(':disabled')) opener.focus();
      else dom.querySelector<HTMLElement>('h1')?.focus();
    };
  }, [opener]);
  useEffect(() => {
    if (!loading && window.document.activeElement === dialog.current) closeButton.current?.focus();
  }, [loading]);
  useEffect(() => {
    if (confirmation) confirmBox.current?.querySelector<HTMLButtonElement>('button')?.focus();
    else if (dialog.current && confirmBox.current === null && !dialog.current.contains(window.document.activeElement)) closeButton.current?.focus();
  }, [confirmation]);
  useEffect(() => { editor.current?.scrollTo({ top: 0 }); }, [pageNumber]);

  const close = () => { if (!pending.current && !busy) { if (changed.length || attempt) setConfirmation('close'); else if (needsReaderSync.current) onUncertainClose(); else onClose(); } };
  const save = async () => {
    if (!source || pending.current || stale || (!attempt && (!changed.length || validation))) return;
    const payload = attempt ?? proofreadPayload(source, pages, crypto.randomUUID());
    const controller = new AbortController(); saveController.current = controller;
    pending.current = true; setSaving(true); setAttempt(payload); setError(''); setCopied(false);
    try {
      const value = await request<ReadingDocument>(`/documents/${documentId}/proofread`, { method: 'PUT', body: JSON.stringify(payload), signal: controller.signal });
      if (!active.current || controller.signal.aborted) return;
      // The write is confirmed. Updating the reader is synchronous and never replays this PUT.
      setAttempt(null); callbacks.current.onSaved(value);
    } catch (e) {
      if (!active.current || controller.signal.aborted) return;
      if (e instanceof ApiError && e.status === 409) { needsReaderSync.current = true; setStale(true); setAttempt(null); }
      else if (e instanceof ApiError && [400, 401, 403, 404, 413, 422, 429].includes(e.status)) setAttempt(null);
      // An interrupted or server-error response can follow a committed write. Keep its receipt and exact body.
      setError(errorMessage(e));
    } finally { if (active.current && !controller.signal.aborted) { pending.current = false; setSaving(false); } }
  };
  const confirm = () => {
    if (pending.current || busy) return;
    const action = confirmation; setConfirmation(null);
    if (action === 'close') { if (attempt || needsReaderSync.current) onUncertainClose(); else onClose(); }
    else if (action === 'reset') {
      const original = source?.pages.find(item => item.number === pageNumber);
      if (original) setPages(items => items.map(item => item.number === pageNumber ? { ...original } : item));
      setError(''); setCopied(false); editor.current?.focus();
    } else if (action === 'reload') void load();
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(pages.map(item => `--- 第 ${item.number} 页 ---\n${item.text}`).join('\n\n')); setCopied(true); }
    catch { setError('无法自动复制。请在正文框中选中文字后手动复制。'); editor.current?.focus(); editor.current?.select(); }
  };
  const confirmationTitle = confirmation === 'close' ? attempt ? '保存结果尚未确认，仍要退出？' : needsReaderSync.current ? '正文版本已变化，退出并读取最新版本？' : '放弃本次未保存的修改？' : confirmation === 'reset' ? '还原这一页的正文？' : '重新读取最新正文？';
  const confirmationDescription = confirmation === 'close' ? attempt ? '服务可能已经保存。退出后会重新核对阅读正文；本次草稿将被丢弃，建议先取消并重试确认保存。' : needsReaderSync.current ? '本次草稿不会保存，退出后将读取服务器上的最新正文。需要保留内容时，请先取消并复制全部正文。' : '未保存的修改会被丢弃，已保存的正文和原 PDF 不受影响。' : confirmation === 'reset' ? '仅还原当前页到打开工作台时的正文，其他页的草稿会保留。' : '本次所有草稿将被替换。需要保留内容时，请先取消并复制全部正文。';
  const confirmationAction = confirmation === 'close' ? attempt ? '退出并核对正文' : needsReaderSync.current ? '退出并读取最新正文' : '放弃修改并退出' : confirmation === 'reset' ? '确认还原本页' : '确认重新读取';

  return createPortal(<div className="proofread-backdrop" onClick={event => { if (event.target === event.currentTarget) close(); }} onDragOver={event => event.preventDefault()} onDrop={event => event.preventDefault()}>
    <section className="proofread-workbench" ref={dialog} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1}>
      <header className="proofread-heading"><div className="proofread-heading-icon"><FileCheck2 size={23} /></div><div><p className="overline">A LITTLE CARE, A CLEARER PAGE</p><h2 id={titleId}>OCR 校对工作台</h2><p className="proofread-document-title" title={title}>{title}</p></div><button ref={closeButton} className="icon-button" aria-label="关闭校对工作台" disabled={busy} onClick={close}><X size={21} /></button></header>
      <p className="proofread-intro" id={descriptionId}>对照原页，修正错字与断行。校对不会重复累计词频或改变复习排期，原 PDF 保留。</p>
      {loading && <div className="proofread-loading" role="status"><LoaderCircle size={24} className="spin" /><p>正在读取逐页正文…</p></div>}
      {!loading && !source && <div className="proofread-loading"><AlertCircle size={27} /><p role="alert">{error}</p><button className="button secondary" onClick={() => void load()}>重新加载校对正文</button></div>}
      {source && <>
        <div className="proofread-toolbar"><div className="proofread-navigation"><button className="icon-button" aria-label="校对上一页" disabled={busy || pageNumber <= 1} onClick={() => setPageNumber(number => number - 1)}><ChevronLeft size={18} /></button><label><span className="visually-hidden">校对页码</span><select aria-label="校对页码" disabled={busy} value={pageNumber} onChange={event => setPageNumber(Number(event.target.value))}>{pages.map(item => <option value={item.number} key={item.number}>第 {item.number} 页{changed.includes(item.number) ? ' · 已修改' : ''}</option>)}</select><span>/ {pages.length} 页</span></label><button className="icon-button" aria-label="校对下一页" disabled={busy || pageNumber >= pages.length} onClick={() => setPageNumber(number => number + 1)}><ChevronRight size={18} /></button></div><span className={`proofread-page-status ${currentChanged ? 'is-changed' : ''}`} role="status">{currentChanged ? <><span />本页已修改</> : <><Check size={13} />本页未修改</>}</span><button className="text-button proofread-copy" disabled={busy} onClick={() => void copy()}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? '已复制正文' : '复制全部正文'}</button></div>
        <div className="proofread-columns">
          <section className="proofread-original" aria-label="PDF 原页预览"><div className="proofread-column-heading"><h3><ScanLine size={15} />原 PDF · 第 {pageNumber} 页</h3><div className="proofread-zoom"><button className="icon-button" aria-label="缩小校对原页" disabled={busy || zoom <= 75} onClick={() => setZoom(value => value - 25)}><ZoomOut size={15} /></button><button className="text-button" aria-label="校对原页恢复默认大小" disabled={busy} onClick={() => setZoom(100)}>{zoom}%</button><button className="icon-button" aria-label="放大校对原页" disabled={busy || zoom >= 200} onClick={() => setZoom(value => value + 25)}><ZoomIn size={15} /></button></div></div><div className="proofread-image-scroll" tabIndex={0} aria-label={`第 ${pageNumber} 页原图，可滚动查看`}>
            {imageError ? <div className="proofread-image-message" role="alert"><AlertCircle size={24} /><p>{imageError}</p><button className="button secondary" disabled={busy} onClick={() => setImageAttempt(value => value + 1)}>重试加载原页</button></div> : !imageUrl ? <div className="proofread-image-message" role="status"><LoaderCircle className="spin" size={23} /><p>正在加载原页…</p></div> : <div className="proofread-image-paper" style={{ width: `${zoom}%` }}><img src={imageUrl} alt={`原 PDF 第 ${pageNumber} 页`} draggable={false} onError={() => setImageError('原页图片暂时无法显示，请重试加载。')} /></div>}
          </div><p className="proofread-original-note">原页仅供核对，保存只更新右侧正文。</p></section>
          <section className="proofread-editor"><div className="proofread-column-heading"><h3><FileText size={15} /><label htmlFor={editorId}>校对正文 · 第 {pageNumber} 页</label></h3><button className="text-button" disabled={locked || !currentChanged} onClick={() => setConfirmation('reset')}><RotateCcw size={13} />还原本页</button></div><textarea ref={editor} id={editorId} aria-label={`校对第 ${pageNumber} 页正文`} aria-describedby={`${editorId}-hint`} value={page?.text ?? ''} readOnly={locked} spellCheck={false} lang="en" placeholder="这一页没有正文。如果原页有文字，可以在这里补充。" onChange={event => { if (locked) return; const text = normalizeProofreadText(event.target.value); setPages(items => items.map(item => item.number === pageNumber ? { ...item, text } : item)); setError(''); setCopied(false); }} /><p className="proofread-editor-note" id={`${editorId}-hint`}>保留原页顺序，空白页可以留空。换页会保留本次修改。</p></section>
        </div>
        {(error || stale || attempt || validation) && <div className={`proofread-message ${stale ? 'is-stale' : ''}`} role="alert"><AlertCircle size={17} /><div>{error && <p>{error}</p>}{stale ? <><p>正文版本已变化，本次草稿仍保留。可以先复制正文；重新读取最新版本后再校对。</p><button className="text-button" disabled={busy} onClick={() => setConfirmation('reload')}>重新读取最新版本</button></> : attempt ? <p>{saving ? '正在保存全部修改，请保持窗口打开。' : '保存结果尚未确认。正文与本次请求已保留；请重试确认保存，避免重复修改。'}</p> : validation && <p>{validation}</p>}</div></div>}
      </>}
      <footer className="proofread-footer"><div className="proofread-save-summary" role="status">{source ? <><strong>{changed.length ? `${changed.length} 页待保存` : '还没有修改'}</strong><span>整篇 {characters.toLocaleString('en-US')} / {source.max_characters.toLocaleString('en-US')} 字</span></> : <span>校对让下一次阅读更顺畅。</span>}</div><div className="proofread-footer-actions"><button className="button secondary" disabled={busy} onClick={close}>返回阅读</button><button className="button primary" disabled={!source || busy || stale || (!attempt && (!changed.length || Boolean(validation)))} onClick={() => void save()}>{saving ? <LoaderCircle size={16} className="spin" /> : <Save size={16} />}{saving ? '正在保存…' : attempt ? '重试确认保存' : '保存全部修改'}</button></div></footer>
      {confirmation && <div className="proofread-confirm-overlay"><div ref={confirmBox} role="alertdialog" aria-modal="true" aria-labelledby={confirmationId} className="proofread-confirm"><span className="proofread-confirm-icon"><AlertCircle size={26} /></span><h3 id={confirmationId}>{confirmationTitle}</h3><p>{confirmationDescription}</p><div><button className="button secondary" onClick={() => { setConfirmation(null); closeButton.current?.focus(); }}>继续校对</button><button className="button primary" onClick={confirm}>{confirmationAction}</button></div></div></div>}
    </section>
  </div>, window.document.body);
}
