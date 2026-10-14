import { Eye, EyeOff, FileText, Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { useState } from 'react';
import type { Occurrence, ReadingDocument } from './types';

export default function DocumentPanel({ document, selectedId, onSelect }: {
  document: ReadingDocument; selectedId: number | null; onSelect: (word: Occurrence) => void;
}) {
  const [zoom, setZoom] = useState(100);
  const [showBoxes, setShowBoxes] = useState(true);
  const [showText, setShowText] = useState(false);
  const [imageError, setImageError] = useState(false);
  return <section className="document-panel panel">
    <div className="panel-heading"><div><FileText size={16} /><h2>阅读原文</h2></div><span className="tiny-badge">{document.source === 'demo' ? '示例图片 · 已预标注' : document.source}</span></div>
    <div className="document-toolbar">
      <div className="segmented"><button className={!showText ? 'active' : ''} onClick={() => setShowText(false)}>原始图片</button><button className={showText ? 'active' : ''} onClick={() => setShowText(true)}>识别文本</button></div>
      {!showText && <div className="zoom-controls"><button className="icon-button" aria-label="缩小图片" disabled={zoom <= 75} onClick={() => setZoom(v => v - 25)}><ZoomOut size={16} /></button><span>{zoom}%</span><button className="icon-button" aria-label="放大图片" disabled={zoom >= 200} onClick={() => setZoom(v => v + 25)}><ZoomIn size={16} /></button><button className="icon-button" title="适应宽度" aria-label="图片恢复默认大小" onClick={() => setZoom(100)}><Maximize2 size={15} /></button></div>}
    </div>
    {showText ? <div className="ocr-text"><p className="overline">EXTRACTED TEXT</p><p>{document.ocr_text}</p><small>切换至原始图片可定位和标记单词。</small></div>
      : <div className="document-canvas">
        {imageError ? <div className="empty-small"><p>图片暂时无法加载。</p><button className="button secondary" onClick={() => { setImageError(false); }}>重新加载</button></div> :
          <div className="image-stage" style={{ width: `${zoom}%`, aspectRatio: `${document.width} / ${document.height}` }}>
            <img src={document.image_url} alt={`英文阅读原图：${document.title}`} onError={() => setImageError(true)} draggable={false} />
            {showBoxes && document.words.map(word => {
              const [x0, y0, x1, y1] = word.bbox;
              return <button key={word.id}
                className={`word-overlay ${word.status?.toLowerCase() ?? 'unmarked'} ${word.id === selectedId ? 'selected' : ''}`}
                style={{ left: `${x0 / document.width * 100}%`, top: `${y0 / document.height * 100}%`, width: `${(x1 - x0) / document.width * 100}%`, height: `${(y1 - y0) / document.height * 100}%` }}
                aria-label={`选择单词 ${word.word}`} aria-pressed={word.id === selectedId}
                title={`${word.word} · 识别置信度 ${Math.round(word.confidence * 100)}%${word.bbox_source === 'estimated' ? ' · 词框为估算位置' : ''}`}
                onClick={() => onSelect(word)} />;
            })}
          </div>}
      </div>}
    <div className="document-footer"><span><span className="pulse-dot" />点击图片中的单词，开始学习</span><button className="text-button" onClick={() => setShowBoxes(v => !v)}>{showBoxes ? <Eye size={14} /> : <EyeOff size={14} />}{showBoxes ? '隐藏标注' : '显示标注'}</button></div>
  </section>;
}
