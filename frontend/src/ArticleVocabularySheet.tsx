import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { BookOpenText, CheckSquare2, Download, FileText, LoaderCircle, Printer, RefreshCw, Square, X } from 'lucide-react';
import { errorMessage, request } from './api';
import {
  articleVocabularySheetPath,
  downloadArticleVocabularySheet,
  selectedVocabularySheetItems,
  vocabularySheetHeadword,
  vocabularySheetItemKey,
  vocabularySheetMeaning,
} from './articleVocabularyExport';
import type { ArticleVocabularySheetData, ArticleVocabularySheetItem } from './articleVocabularyExport';
import './articleVocabularySheet.css';

export default function ArticleVocabularySheet({ documentId, title, unmarkedCount, disabled = false }: {
  documentId: number;
  title: string;
  unmarkedCount: number;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  return <>
    <div className="article-sheet-trigger-wrap">
      <button ref={opener} type="button" className="article-sheet-trigger" disabled={disabled} onClick={() => setOpen(true)}>
        <BookOpenText size={17} aria-hidden="true" />
        <span><strong>制作生词表</strong><small>{unmarkedCount} 个待收录（未标记）词</small></span>
      </button>
    </div>
    {open && createPortal(<ArticleVocabularySheetDialog documentId={documentId} fallbackTitle={title} opener={opener.current} onClose={() => setOpen(false)} />, document.body)}
  </>;
}

function ArticleVocabularySheetDialog({ documentId, fallbackTitle, opener, onClose }: {
  documentId: number;
  fallbackTitle: string;
  opener: HTMLButtonElement | null;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const printFrames = useRef<number[]>([]);
  const titleId = useId();
  const descriptionId = useId();
  const [sheet, setSheet] = useState<ArticleVocabularySheetData | null>(null);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [downloadError, setDownloadError] = useState('');

  useEffect(() => {
    const modal = dialog.current;
    if (!modal) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    modal.showModal();
    closeButton.current?.focus();
    return () => {
      modal.close();
      document.body.style.overflow = overflow;
      document.body.classList.remove('article-sheet-printing');
      if (opener?.isConnected && !opener.matches(':disabled')) opener.focus();
    };
  }, [opener]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    setSheet(null);
    setSelected(new Set());
    request<ArticleVocabularySheetData>(articleVocabularySheetPath(documentId), { signal: controller.signal })
      .then(value => {
        if (controller.signal.aborted) return;
        setSheet(value);
        setSelected(new Set(value.items.map(vocabularySheetItemKey)));
      })
      .catch(reason => { if (!controller.signal.aborted) setError(errorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [documentId, retry]);

  useEffect(() => {
    const finish = () => document.body.classList.remove('article-sheet-printing');
    window.addEventListener('afterprint', finish);
    return () => {
      window.removeEventListener('afterprint', finish);
      printFrames.current.forEach(frame => window.cancelAnimationFrame(frame));
      printFrames.current = [];
    };
  }, []);

  const selectedItems = useMemo(() => selectedVocabularySheetItems(sheet?.items ?? [], selected), [sheet, selected]);
  const allSelected = Boolean(sheet?.items.length) && selected.size === sheet?.items.length;
  const sheetTitle = sheet?.title || fallbackTitle;
  const toggle = (item: ArticleVocabularySheetItem) => setSelected(current => {
    const next = new Set(current);
    const key = vocabularySheetItemKey(item);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const print = () => {
    if (!selectedItems.length) return;
    printFrames.current.forEach(frame => window.cancelAnimationFrame(frame));
    printFrames.current = [];
    document.body.classList.add('article-sheet-printing');
    // Let the print-only selection and A5 page styles reach layout before opening the system dialog.
    const first = window.requestAnimationFrame(() => {
      const second = window.requestAnimationFrame(() => {
        printFrames.current = [];
        if (!dialog.current?.open) {
          document.body.classList.remove('article-sheet-printing');
          return;
        }
        window.print();
      });
      printFrames.current.push(second);
    });
    printFrames.current.push(first);
  };
  const download = () => {
    if (!selectedItems.length) return;
    setDownloadError('');
    try { downloadArticleVocabularySheet(sheetTitle, selectedItems); }
    catch { setDownloadError('TXT 下载暂时失败，请重试。'); }
  };

  return <dialog ref={dialog} className="article-vocabulary-dialog" aria-labelledby={titleId} aria-describedby={descriptionId}
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => {
      if (event.target !== event.currentTarget) return;
      const rect = event.currentTarget.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) onClose();
    }}
    onKeyDown={event => {
      if (event.key !== 'Tab') return;
      const elements = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
      const first = elements[0], last = elements[elements.length - 1];
      if (!first) { event.preventDefault(); event.currentTarget.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }}>
    <header className="article-vocabulary-heading">
      <div><p><FileText size={13} aria-hidden="true" /> ARTICLE WORD LIST</p><h2 id={titleId}>制作文章生词表</h2></div>
      <button ref={closeButton} type="button" className="icon-button" aria-label="关闭生词表" onClick={onClose}><X size={20} /></button>
    </header>
    <p id={descriptionId} className="article-vocabulary-intro">文章导入时，词间会先记录遇到的词形；这里筛选尚未标为「已掌握 / 不认识 / 不确定」的待收录词。导出只生成便携词表，不会改变学习状态。</p>

    {loading && <div className="article-vocabulary-state" role="status"><LoaderCircle size={24} className="spin" /><strong>正在整理词形与中文释义…</strong><span>本地词典会一次完成匹配。</span></div>}
    {!loading && error && <div className="article-vocabulary-state is-error" role="alert"><strong>暂时没能生成生词表</strong><span>{error}</span><button type="button" className="button secondary" onClick={() => setRetry(value => value + 1)}><RefreshCw size={15} />重新读取</button></div>}
    {!loading && !error && sheet && !sheet.items.length && <div className="article-vocabulary-state"><BookOpenText size={30} /><strong>这篇文章没有待收录词</strong><span>所有识别到的词都已经标记为已掌握、不认识或不确定。</span></div>}

    {!loading && !error && sheet && sheet.items.length > 0 && <>
      <div className="article-vocabulary-toolbar">
        <div><strong>已选 {selected.size} / {sheet.item_count}</strong><span>全文共出现 {sheet.occurrence_count} 次</span></div>
        <div role="group" aria-label="选择生词">
          <button type="button" className="text-button" disabled={allSelected} onClick={() => setSelected(new Set(sheet.items.map(vocabularySheetItemKey)))}><CheckSquare2 size={14} />全选</button>
          <button type="button" className="text-button" disabled={!selected.size} onClick={() => setSelected(new Set())}><Square size={14} />清空</button>
        </div>
      </div>
      <section className="article-vocabulary-paper" aria-label="生词表预览">
        <header><p>WORD LIST · 待收录词汇</p><h3>{sheetTitle}</h3><span>按词语在文章中首次出现的顺序排列</span></header>
        <ol>
          {sheet.items.map((item, index) => {
            const key = vocabularySheetItemKey(item);
            const checked = selected.has(key);
            const phonetic = item.phonetic?.replace(/^\/+|\/+$/g, '').trim();
            return <li key={key} className={checked ? 'is-selected' : ''}>
              <label>
                <input type="checkbox" checked={checked} onChange={() => toggle(item)} aria-label={`选择 ${vocabularySheetHeadword(item)}`} />
                <span className="article-vocabulary-number">{String(index + 1).padStart(2, '0')}</span>
                <span className="article-vocabulary-entry"><span className="article-vocabulary-word"><strong>{vocabularySheetHeadword(item)}</strong>{phonetic && <em>/{phonetic}/</em>}</span><span className="article-vocabulary-meaning">{vocabularySheetMeaning(item)}</span></span>
                {item.count > 1 && <small>×{item.count}</small>}
              </label>
            </li>;
          })}
        </ol>
        <footer><span>词间 Lexi</span><span>{selected.size} 个词 · 可裁切为随身词表</span></footer>
      </section>
      {sheet.dictionary_warning && <p className="article-vocabulary-dictionary-warning" role="status">{sheet.dictionary_warning}</p>}
      {!selected.size && <p className="article-vocabulary-selection-warning" role="status">至少选择一个词后才能导出。</p>}
      {downloadError && <p className="modal-error" role="alert">{downloadError}</p>}
    </>}

    <footer className="article-vocabulary-actions">
      <p>打印窗口中选择“另存为 PDF”，纸张建议使用 A5。</p>
      <div><button type="button" className="button secondary" onClick={onClose}>取消</button><button type="button" className="button secondary" disabled={!selectedItems.length} onClick={download}><Download size={16} />下载 TXT</button><button type="button" className="button primary" disabled={!selectedItems.length} onClick={print}><Printer size={16} />打印 / 另存 PDF</button></div>
    </footer>
  </dialog>;
}
