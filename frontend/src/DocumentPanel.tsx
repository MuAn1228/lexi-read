import { ChevronLeft, ChevronRight, Download, ExternalLink, Eye, EyeOff, FileCheck2, FileText, Globe, LoaderCircle, Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { errorMessage, requestFile, requestImage } from './api';
import { articleParts } from './textArticle';
import { pdfPageContent, pdfPageForOccurrence } from './pdfArticle';
import { originalWebLink } from './urlArticle';
import { hasCorrectedImageText, isImageArticle } from './imageArticle';
import { highlightParts, validArticleFocus } from './articleQuestions';
import type { ArticleFocus } from './articleQuestions';
import { readingDocumentIdentity } from './readingResume';
import type { ReadingPosition } from './readingResume';
import type { Occurrence, ReadingDocument } from './types';

export default function DocumentPanel({ document, selectedId, revealToken, restartToken = 0, citationFocus = null, disabled = false, resumePosition = null, onProofread, onSelect, onReadingPosition, onReadingRestored }: {
  document: ReadingDocument; selectedId: number | null; revealToken: number; restartToken?: number; citationFocus?: ArticleFocus | null; onSelect: (word: Occurrence | null) => void;
  disabled?: boolean; resumePosition?: ReadingPosition | null; onProofread?: (page: number, opener: HTMLElement) => void;
  onReadingPosition?: (position: ReadingPosition) => void; onReadingRestored?: () => void;
}) {
  const isPdf = document.source === 'pdf';
  const isWeb = document.source === 'web';
  const isText = document.source === 'text' || isPdf || isWeb;
  const isImage = isImageArticle(document.source);
  const corrected = hasCorrectedImageText(document);
  const contentIdentity = useMemo(() => readingDocumentIdentity(document), [document.source, document.ocr_text, document.words, document.pages, document.width, document.height]);
  const resume = resumePosition?.documentId === document.id && resumePosition.contentIdentity === contentIdentity ? resumePosition : null;
  const pendingResume = useRef<ReadingPosition | null>(resume);
  const restoring = useRef(Boolean(resume));
  const restored = useRef(false);
  const captureTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const restoreFrames = useRef<number[]>([]);
  const restartSeen = useRef(restartToken);
  const pendingRestart = useRef(false);
  const [zoom, setZoom] = useState(() => resume?.zoom ?? 100);
  const [showBoxes, setShowBoxes] = useState(true);
  const [showText, setShowText] = useState(() => isText || (resume ? resume.surface === 'text' : corrected));
  const [imageError, setImageError] = useState('');
  const [imageAttempt, setImageAttempt] = useState(0);
  const [image, setImage] = useState<{ source: string; url: string } | null>(null);
  const [loadedImageUrl, setLoadedImageUrl] = useState<string | null>(null);
  const article = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  const textVisible = isText || showText;
  const webLink = isWeb ? originalWebLink(document.source_url) : null;
  const pages = document.pages ?? [];
  const [pageNumber, setPageNumber] = useState(() => isPdf
    ? pages.some(page => page.number === resume?.pageNumber) ? resume!.pageNumber : pdfPageForOccurrence(document, selectedId) ?? pages[0]?.number ?? 1
    : 1);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const downloadController = useRef<AbortController | null>(null);
  const downloadUrl = useRef<string | null>(null);
  const page = pages.find(item => item.number === pageNumber);
  const pageContent = useMemo(() => isPdf && page ? pdfPageContent(document.ocr_text, document.words, page) : { text: document.ocr_text, words: document.words }, [isPdf, page, document.ocr_text, document.words]);
  const parts = useMemo(() => articleParts(pageContent.text, pageContent.words), [pageContent]);
  const selectableText = parts.some(part => part.occurrence !== null);
  const focus = validArticleFocus(document, citationFocus) ? citationFocus : null;
  const pageStart = isPdf ? page?.text_start ?? 0 : 0;
  const renderedParts = useMemo(() => {
    let offset = pageStart;
    return parts.map(part => {
      const highlighted = highlightParts(part.text, offset, focus);
      offset += Array.from(part.text).length;
      return { ...part, highlighted };
    });
  }, [parts, pageStart, focus]);
  const imageUrl = image?.source === document.image_url ? image.url : null;
  const imageReady = Boolean(imageUrl && loadedImageUrl === imageUrl);
  useEffect(() => {
    setImage(null); setImageError('');
    const source = document.image_url;
    if (isText || !source) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    let active = true;
    void requestImage(source, controller.signal).then(blob => {
      if (!active) return;
      objectUrl = URL.createObjectURL(blob);
      setImage({ source, url: objectUrl });
    }).catch(error => {
      if (active && !controller.signal.aborted) setImageError(errorMessage(error));
    });
    return () => { active = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [document.image_url, imageAttempt, isText]);
  useEffect(() => {
    if (!pendingResume.current) setShowText(corrected);
    setShowBoxes(true);
  }, [revealToken, corrected]);
  useEffect(() => {
    // Corrected words have no coordinates in the original image. A word or bookmark opens its text position.
    if (corrected && selectedId !== null && pendingResume.current?.surface !== 'image') setShowText(true);
  }, [corrected, selectedId, revealToken]);
  useEffect(() => {
    if (!isPdf || focus || pendingResume.current) return;
    const selectedPage = pdfPageForOccurrence(document, selectedId);
    if (selectedPage !== null) setPageNumber(selectedPage);
  }, [document.id, selectedId, isPdf, revealToken, focus?.token]);
  useEffect(() => {
    if (!focus) return;
    if (isPdf) {
      const citedPage = pages.find(item => focus.start >= item.text_start && focus.end <= item.text_end);
      if (citedPage) setPageNumber(citedPage.number);
    } else if (!isText) setShowText(true);
  // A citation click changes the token even if the same quote is opened again.
  }, [focus?.token, isPdf, isText]);
  useEffect(() => {
    if (!focus) return;
    const container = article.current;
    const mark = container?.querySelector<HTMLElement>('[data-article-citation]');
    if (!container || !mark) return;
    panel.current?.scrollIntoView({ block: 'start', behavior: 'instant' });
    const surface = container.getBoundingClientRect(); const target = mark.getBoundingClientRect();
    container.scrollTop += target.top - surface.top - container.clientHeight / 3;
    mark.focus({ preventScroll: true });
  }, [focus?.token, pageNumber, textVisible]);
  useEffect(() => () => {
    downloadController.current?.abort();
    if (downloadUrl.current) URL.revokeObjectURL(downloadUrl.current);
  }, []);
  const changePage = (number: number) => {
    const target = pages.find(item => item.number === number);
    if (!target) return;
    setPageNumber(number);
    const word = document.words.find(item => item.text_start !== null && item.text_start !== undefined && item.text_end !== null && item.text_end !== undefined && item.text_start >= target.text_start && item.text_end <= target.text_end);
    onSelect(word ?? null);
    if (article.current) article.current.scrollTop = 0;
  };
  const downloadPdf = async () => {
    if (downloadController.current || !document.pdf_url) return;
    const controller = new AbortController(); downloadController.current = controller;
    setDownloading(true); setDownloadError('');
    try {
      const blob = await requestFile(document.pdf_url, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (downloadUrl.current) URL.revokeObjectURL(downloadUrl.current);
      const url = URL.createObjectURL(blob); downloadUrl.current = url;
      const link = window.document.createElement('a');
      link.href = url; link.download = `${document.title.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').replace(/\.pdf$/i, '') || '阅读材料'}.pdf`;
      link.click();
    } catch (error) { if (!controller.signal.aborted) setDownloadError(errorMessage(error)); }
    finally { if (!controller.signal.aborted) { downloadController.current = null; setDownloading(false); } }
  };
  useEffect(() => {
    if (selectedId === null || focus) return;
    const container = textVisible ? article.current : canvas.current;
    const button = container?.querySelector<HTMLButtonElement>(`[data-occurrence-id="${selectedId}"]`);
    if (!container || !button) return;
    // Scroll only the reading surface so choosing a word does not jump the entire page.
    const surface = container.getBoundingClientRect(); const word = button.getBoundingClientRect();
    if (word.top < surface.top || word.bottom > surface.bottom) container.scrollTop += word.top - surface.top - container.clientHeight / 2 + word.height / 2;
    if (word.left < surface.left || word.right > surface.right) container.scrollLeft += word.left - surface.left - container.clientWidth / 2 + word.width / 2;
  }, [selectedId, textVisible, revealToken, imageUrl, imageReady, showBoxes, zoom, pageNumber, focus?.token]);

  const capturePosition = useCallback((): ReadingPosition | null => {
    const surface: ReadingPosition['surface'] = textVisible ? 'text' : 'image';
    if (surface === 'image' && !imageReady) return null;
    const container = surface === 'text' ? article.current : canvas.current;
    if (!container) return null;
    const bounds = container.getBoundingClientRect();
    const candidates = Array.from(container.querySelectorAll<HTMLElement>('[data-occurrence-id]'));
    const anchor = candidates.find(element => {
      const rect = element.getBoundingClientRect();
      return rect.bottom > bounds.top + 1 && rect.right > bounds.left + 1 && rect.top < bounds.bottom && rect.left < bounds.right;
    }) ?? candidates.find(element => element.getBoundingClientRect().top >= bounds.top - 1) ?? null;
    const anchorId = anchor ? Number(anchor.dataset.occurrenceId) : null;
    const rect = anchor?.getBoundingClientRect();
    return {
      documentId: document.id,
      contentIdentity,
      selectedId,
      surface,
      pageNumber: isPdf ? pageNumber : 1,
      zoom,
      anchorId: anchorId && Number.isSafeInteger(anchorId) && anchorId > 0 ? anchorId : null,
      anchorOffset: rect ? rect.top - bounds.top : 0,
      anchorLeftOffset: rect ? rect.left - bounds.left : 0,
      fraction: container.scrollTop / Math.max(1, container.scrollHeight - container.clientHeight),
      horizontalFraction: container.scrollLeft / Math.max(1, container.scrollWidth - container.clientWidth),
      updatedAt: Date.now(),
    };
  }, [contentIdentity, document.id, imageReady, isPdf, pageNumber, selectedId, textVisible, zoom]);

  const flushPosition = useCallback(() => {
    if (captureTimer.current) { clearTimeout(captureTimer.current); captureTimer.current = null; }
    if (restoring.current) return;
    const position = capturePosition();
    if (position) onReadingPosition?.(position);
  }, [capturePosition, onReadingPosition]);
  const schedulePosition = useCallback(() => {
    if (restoring.current || !onReadingPosition) return;
    if (captureTimer.current) clearTimeout(captureTimer.current);
    captureTimer.current = setTimeout(flushPosition, 180);
  }, [flushPosition, onReadingPosition]);

  useEffect(() => {
    const saved = pendingResume.current;
    if (!saved || restored.current || focus) return;
    if ((saved.surface === 'image' && (!imageReady || textVisible)) || (saved.surface === 'text' && !textVisible)) return;
    if (isPdf && pageNumber !== saved.pageNumber) return;
    const container = saved.surface === 'text' ? article.current : canvas.current;
    if (!container) return;
    const first = window.requestAnimationFrame(() => {
      const anchor = saved.anchorId === null ? null : container.querySelector<HTMLElement>(`[data-occurrence-id="${saved.anchorId}"]`);
      if (anchor) {
        const bounds = container.getBoundingClientRect();
        const rect = anchor.getBoundingClientRect();
        container.scrollTop += rect.top - bounds.top - saved.anchorOffset;
        container.scrollLeft += rect.left - bounds.left - saved.anchorLeftOffset;
      } else {
        container.scrollTop = saved.fraction * Math.max(0, container.scrollHeight - container.clientHeight);
        container.scrollLeft = saved.horizontalFraction * Math.max(0, container.scrollWidth - container.clientWidth);
      }
      const second = window.requestAnimationFrame(() => {
        restored.current = true; pendingResume.current = null; restoring.current = false;
        onReadingRestored?.(); flushPosition();
      });
      restoreFrames.current.push(second);
    });
    restoreFrames.current.push(first);
  }, [flushPosition, focus, imageReady, isPdf, onReadingRestored, pageNumber, textVisible]);

  useEffect(() => {
    if (restartSeen.current !== restartToken) {
      restartSeen.current = restartToken; pendingRestart.current = true;
      restoreFrames.current.forEach(frame => window.cancelAnimationFrame(frame)); restoreFrames.current = [];
      pendingResume.current = null; restoring.current = false; restored.current = true;
    }
    if (!pendingRestart.current) return;
    const firstPage = pages[0]?.number ?? 1;
    if (isPdf && pageNumber !== firstPage) { setPageNumber(firstPage); return; }
    const container = textVisible ? article.current : canvas.current;
    if (!container || (!textVisible && !imageReady)) return;
    const frame = window.requestAnimationFrame(() => {
      container.scrollTop = 0; container.scrollLeft = 0; pendingRestart.current = false; flushPosition();
    });
    restoreFrames.current.push(frame);
  }, [flushPosition, imageReady, isPdf, pageNumber, pages, restartToken, textVisible]);

  useEffect(() => {
    if (restoring.current) return;
    schedulePosition();
  }, [pageNumber, selectedId, textVisible, zoom, schedulePosition]);
  useEffect(() => {
    const flushHidden = () => { if (window.document.visibilityState === 'hidden') flushPosition(); };
    window.addEventListener('pagehide', flushPosition);
    window.document.addEventListener('visibilitychange', flushHidden);
    return () => {
      window.removeEventListener('pagehide', flushPosition);
      window.document.removeEventListener('visibilitychange', flushHidden);
      flushPosition();
    };
  }, [flushPosition]);
  useEffect(() => () => {
    if (captureTimer.current) clearTimeout(captureTimer.current);
    restoreFrames.current.forEach(frame => window.cancelAnimationFrame(frame));
  }, []);
  return <section ref={panel} className={`document-panel panel ${textVisible ? 'text-document-panel' : ''} ${isPdf ? 'pdf-document-panel' : ''}`}>
    <div className="panel-heading"><div><FileText size={16} /><h2>阅读原文</h2></div><span className="tiny-badge">{isPdf ? `PDF · ${pages.length} 页` : isWeb ? '网页文章' : isText ? '文字文章' : corrected ? '图片 · 已校对' : document.source === 'demo' ? '示例图片 · 已预标注' : '图片文章'}</span></div>
    {isImage && <div className="image-proofread-toolbar"><div className="segmented"><button disabled={disabled} className={!showText ? 'active' : ''} aria-pressed={!showText} onClick={() => setShowText(false)}>原始图片</button><button disabled={disabled} className={showText ? 'active' : ''} aria-pressed={showText} onClick={() => setShowText(true)}>{corrected ? '阅读正文' : '识别文本'}</button></div>{onProofread && <button className="text-button pdf-proofread-entry" aria-label="校对图片正文" disabled={disabled} onClick={event => onProofread(1, event.currentTarget)}><FileCheck2 size={14} />校对正文</button>}</div>}
    <div className="reader-surfaces">
    <div className={`reader-surface reader-text-surface ${textVisible ? 'is-current' : ''}`} inert={!textVisible} aria-hidden={!textVisible}>
      {isPdf && <div className="pdf-page-toolbar"><div className="pdf-page-controls"><button className="icon-button" aria-label="PDF 上一页" disabled={disabled || pageNumber <= 1} onClick={() => changePage(pageNumber - 1)}><ChevronLeft size={17} /></button><label><select aria-label="PDF 页码" disabled={disabled} value={pageNumber} onChange={event => changePage(Number(event.target.value))}>{pages.map(item => <option key={item.number} value={item.number}>第 {item.number} 页</option>)}</select><span>/ {pages.length} 页</span></label><button className="icon-button" aria-label="PDF 下一页" disabled={disabled || pageNumber >= pages.length} onClick={() => changePage(pageNumber + 1)}><ChevronRight size={17} /></button></div><div className="pdf-proofread-actions">{onProofread && <button className="text-button pdf-proofread-entry" aria-label="校对 PDF 正文" disabled={disabled} onClick={event => onProofread(pageNumber, event.currentTarget)}><FileCheck2 size={14} />校对正文</button>}<button className="text-button" aria-label="下载原 PDF" disabled={downloading || !document.pdf_url} onClick={() => void downloadPdf()}>{downloading ? <LoaderCircle size={14} className="spin" /> : <Download size={14} />}下载原 PDF</button></div></div>}
      {webLink && <div className="web-source-toolbar"><span><Globe size={14} />{webLink.host}</span><a className="text-button" href={webLink.href} target="_blank" rel="noopener noreferrer" aria-label="在新窗口打开原网页">查看原网页<ExternalLink size={14} /></a></div>}
      <p className="text-reading-guide">{isPdf ? 'PDF 正文 · 按原文件页码阅读。扫描识别结果请结合原文件核对。' : isWeb ? '网页正文已保存 · 点击英文单词，在原句中学习。' : corrected ? '已校对的阅读正文 · 点击单词学习。原始图片保留，原图词框已停用。' : isImage && !selectableText ? '图片识别正文 · 可先校对错字和断行，保存后即可在正文中点击单词学习。' : '点击英文单词，查看它在这句话里的意思。'}</p>
      {downloadError && <p className="pdf-download-error" role="alert">{downloadError} 可点击“下载原 PDF”重试。</p>}
      <div className="text-article" ref={article} onScroll={schedulePosition} aria-label={isPdf ? `PDF 第 ${pageNumber} 页正文` : isImage ? corrected ? '图片校对正文' : '图片识别正文' : '文章正文'} lang="en">{isPdf && !pageContent.text.trim() ? <div className="pdf-empty-page" lang="zh-CN"><FileText size={29} /><p>这一页没有可提取的文字</p><span>可能是空白页，或仅提取文字时跳过的扫描页。可继续翻页或下载原 PDF 查看。</span></div> : renderedParts.map((part, index) => part.occurrence ? <button key={`word-${part.occurrence.id}`} type="button"
        className={`article-word ${part.occurrence.status?.toLowerCase() ?? 'unmarked'} ${part.occurrence.id === selectedId ? 'selected' : ''}`}
        data-occurrence-id={part.occurrence.id} disabled={disabled} aria-label={`选择单词 ${part.text}`} aria-pressed={part.occurrence.id === selectedId}
        onClick={() => onSelect(part.occurrence!)}>{part.highlighted.map((piece, number) => piece.highlighted ? <mark className="article-citation-highlight" data-article-citation="true" tabIndex={-1} key={number}>{piece.text}</mark> : piece.text)}</button> : <span key={`text-${index}`}>{part.highlighted.map((piece, number) => piece.highlighted ? <mark className="article-citation-highlight" data-article-citation="true" tabIndex={-1} key={number}>{piece.text}</mark> : piece.text)}</span>)}</div>
    </div>
    {!isText && <div className={`reader-surface reader-image-surface ${!textVisible ? 'is-current' : ''}`} inert={textVisible} aria-hidden={textVisible}>
      <div className="document-toolbar"><div className="zoom-controls"><button className="icon-button" aria-label="缩小图片" disabled={disabled || zoom <= 75} onClick={() => setZoom(v => v - 25)}><ZoomOut size={16} /></button><span>{zoom}%</span><button className="icon-button" aria-label="放大图片" disabled={disabled || zoom >= 200} onClick={() => setZoom(v => v + 25)}><ZoomIn size={16} /></button><button className="icon-button" title="适应宽度" aria-label="图片恢复默认大小" disabled={disabled} onClick={() => setZoom(100)}><Maximize2 size={15} /></button></div>{corrected && <span className="image-corrected-note">原图保留 · 请在阅读正文中选词</span>}</div>
      <div className="document-canvas" ref={canvas} onScroll={schedulePosition} aria-busy={!imageError && !imageReady}>
        {imageError ? <div className="empty-small" role="alert"><p>{imageError}</p><button className="button secondary" onClick={() => setImageAttempt(value => value + 1)}>重新加载</button></div> :
          <div className={`image-stage ${imageReady ? 'is-ready' : 'is-loading'}`} style={{ width: `${zoom}%`, aspectRatio: `${document.width} / ${document.height}` }}>
            {imageUrl && <img src={imageUrl} alt={`英文阅读原图：${document.title}`} onLoad={() => setLoadedImageUrl(imageUrl)} onError={() => setImageError('图片暂时无法加载。')} draggable={false} />}
            {!imageReady && <div className="reader-image-placeholder" role="status"><FileText size={22} aria-hidden="true" /><p>正在加载原图…</p><div className="reader-image-lines" aria-hidden="true"><span /><span /><span /><span /><span /></div></div>}
            {imageReady && showBoxes && !corrected && document.words.map(word => {
              if (!word.bbox || word.bbox_source === 'corrected_text') return null;
              const [x0, y0, x1, y1] = word.bbox;
              return <button key={word.id}
                data-occurrence-id={word.id}
                className={`word-overlay ${word.status?.toLowerCase() ?? 'unmarked'} ${word.id === selectedId ? 'selected' : ''}`}
                style={{ left: `${x0 / document.width * 100}%`, top: `${y0 / document.height * 100}%`, width: `${(x1 - x0) / document.width * 100}%`, height: `${(y1 - y0) / document.height * 100}%` }}
                aria-label={`选择单词 ${word.word}${word.status ? `，${word.status === 'KNOWN' ? '已掌握' : word.status === 'UNKNOWN' ? '不认识' : '不确定'}` : '，未标记'}`} aria-pressed={word.id === selectedId} disabled={disabled}
                title={`${word.word}${word.confidence === null ? '' : ` · 识别置信度 ${Math.round(word.confidence * 100)}%`}${word.bbox_source === 'estimated' ? ' · 词框为估算位置' : ''}`}
                onClick={() => onSelect(word)} />;
            })}
          </div>}
      </div></div>}
    </div>
    <div className="document-footer"><span><span className="pulse-dot" />{corrected && !textVisible ? '切换到阅读正文，点击单词学习' : isImage && textVisible && !selectableText ? '对照原图核对识别文字' : `点击${textVisible ? '正文' : '图片'}中的单词，开始学习`}</span>{textVisible || corrected ? <span>{isPdf ? `本页 ${pageContent.words.length}` : document.words.length} 处英文单词</span> : <button className="text-button" disabled={disabled} aria-pressed={showBoxes} onClick={() => setShowBoxes(v => !v)}>{showBoxes ? <Eye size={14} /> : <EyeOff size={14} />}{showBoxes ? '隐藏标注' : '显示标注'}</button>}</div>
  </section>;
}
