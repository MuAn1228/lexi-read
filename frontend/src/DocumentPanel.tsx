import { Eye, EyeOff, FileText, Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { errorMessage, requestImage } from './api';
import { articleParts } from './textArticle';
import type { Occurrence, ReadingDocument } from './types';

export default function DocumentPanel({ document, selectedId, onSelect }: {
  document: ReadingDocument; selectedId: number | null; onSelect: (word: Occurrence) => void;
}) {
  const [zoom, setZoom] = useState(100);
  const [showBoxes, setShowBoxes] = useState(true);
  const [showText, setShowText] = useState(false);
  const [imageError, setImageError] = useState('');
  const [imageAttempt, setImageAttempt] = useState(0);
  const [image, setImage] = useState<{ source: string; url: string } | null>(null);
  const article = useRef<HTMLDivElement>(null);
  const isText = document.source === 'text';
  const parts = useMemo(() => isText ? articleParts(document.ocr_text, document.words) : [], [isText, document.ocr_text, document.words]);
  const imageUrl = image?.source === document.image_url ? image.url : null;
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
    if (isText && selectedId !== null) article.current?.querySelector<HTMLButtonElement>(`[data-occurrence-id="${selectedId}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [selectedId, isText]);
  return <section className={`document-panel panel ${isText ? 'text-document-panel' : ''}`}>
    <div className="panel-heading"><div><FileText size={16} /><h2>阅读原文</h2></div><span className="tiny-badge">{isText ? '文字文章' : document.source === 'demo' ? '示例图片 · 已预标注' : '图片文章'}</span></div>
    {isText ? <>
      <p className="text-reading-guide">点击英文单词，查看它在这句话里的意思。</p>
      <div className="text-article" ref={article} aria-label="文章正文" lang="en">{parts.map((part, index) => part.occurrence ? <button key={`word-${part.occurrence.id}`} type="button"
        className={`article-word ${part.occurrence.status?.toLowerCase() ?? 'unmarked'} ${part.occurrence.id === selectedId ? 'selected' : ''}`}
        data-occurrence-id={part.occurrence.id} aria-label={`选择单词 ${part.text}`} aria-pressed={part.occurrence.id === selectedId}
        onClick={() => onSelect(part.occurrence!)}>{part.text}</button> : <span key={`text-${index}`}>{part.text}</span>)}</div>
    </> : <><div className="document-toolbar">
      <div className="segmented"><button className={!showText ? 'active' : ''} onClick={() => setShowText(false)}>原始图片</button><button className={showText ? 'active' : ''} onClick={() => setShowText(true)}>识别文本</button></div>
      {!showText && <div className="zoom-controls"><button className="icon-button" aria-label="缩小图片" disabled={zoom <= 75} onClick={() => setZoom(v => v - 25)}><ZoomOut size={16} /></button><span>{zoom}%</span><button className="icon-button" aria-label="放大图片" disabled={zoom >= 200} onClick={() => setZoom(v => v + 25)}><ZoomIn size={16} /></button><button className="icon-button" title="适应宽度" aria-label="图片恢复默认大小" onClick={() => setZoom(100)}><Maximize2 size={15} /></button></div>}
    </div>
    {showText ? <div className="ocr-text"><p className="overline">EXTRACTED TEXT</p><p>{document.ocr_text}</p><small>切换至原始图片可定位和标记单词。</small></div>
      : <div className="document-canvas">
        {imageError ? <div className="empty-small" role="alert"><p>{imageError}</p><button className="button secondary" onClick={() => setImageAttempt(value => value + 1)}>重新加载</button></div> : !imageUrl ? <div className="empty-small" role="status"><p>正在加载原图…</p></div> :
          <div className="image-stage" style={{ width: `${zoom}%`, aspectRatio: `${document.width} / ${document.height}` }}>
            <img src={imageUrl} alt={`英文阅读原图：${document.title}`} onError={() => setImageError('图片暂时无法加载。')} draggable={false} />
            {showBoxes && document.words.map(word => {
              if (!word.bbox) return null;
              const [x0, y0, x1, y1] = word.bbox;
              return <button key={word.id}
                className={`word-overlay ${word.status?.toLowerCase() ?? 'unmarked'} ${word.id === selectedId ? 'selected' : ''}`}
                style={{ left: `${x0 / document.width * 100}%`, top: `${y0 / document.height * 100}%`, width: `${(x1 - x0) / document.width * 100}%`, height: `${(y1 - y0) / document.height * 100}%` }}
                aria-label={`选择单词 ${word.word}`} aria-pressed={word.id === selectedId}
                title={`${word.word}${word.confidence === null ? '' : ` · 识别置信度 ${Math.round(word.confidence * 100)}%`}${word.bbox_source === 'estimated' ? ' · 词框为估算位置' : ''}`}
                onClick={() => onSelect(word)} />;
            })}
          </div>}
      </div>}</>}
    <div className="document-footer"><span><span className="pulse-dot" />点击{isText ? '文章' : '图片'}中的单词，开始学习</span>{isText ? <span>{document.words.length} 处英文单词</span> : <button className="text-button" onClick={() => setShowBoxes(v => !v)}>{showBoxes ? <Eye size={14} /> : <EyeOff size={14} />}{showBoxes ? '隐藏标注' : '显示标注'}</button>}</div>
  </section>;
}
