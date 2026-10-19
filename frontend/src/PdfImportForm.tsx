import { FileText, LoaderCircle, ScanText, Upload } from 'lucide-react';
import { useId, useRef, useState } from 'react';
import type { PdfRecognitionMode } from './pdfArticle';
import './pdfScan.css';

const recognitionOptions: { value: PdfRecognitionMode; title: string; description: string }[] = [
  { value: 'auto', title: '自动识别', description: '先提取文字，扫描页自动用 OCR 识别。' },
  { value: 'text', title: '仅提取文字', description: '适合能选中文字的 PDF；扫描页不识别。' },
  { value: 'scan', title: '全部用 OCR', description: '逐页识别图片，适合扫描件或文字层异常的 PDF。' },
];

export default function PdfImportForm({ file, mode, disabled, processing = false, onModeChange, onChoose, onSubmit }: {
  file: File | null; mode: PdfRecognitionMode; disabled: boolean; processing?: boolean;
  onModeChange: (mode: PdfRecognitionMode) => void; onChoose: (file: File) => void; onSubmit: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const modeGroup = useId();
  const [dragOver, setDragOver] = useState(false);
  const busy = disabled || processing;
  return <div className="pdf-import-form" aria-busy={processing}>
    <p className="text-import-intro">教材、试卷、论文，扫描版也能读。保留原页码，把英文正文带进你的阅读空间。</p>
    <div className={`upload-zone pdf-upload-zone ${dragOver && !busy ? 'drag-over' : ''}`} onDragOver={event => { event.preventDefault(); if (!busy) setDragOver(true); }} onDragLeave={() => setDragOver(false)} onDrop={event => { event.preventDefault(); setDragOver(false); if (!busy && event.dataTransfer.files[0]) onChoose(event.dataTransfer.files[0]); }}>
      <div className="upload-icon"><ScanText size={25} aria-hidden="true" /></div><h3>拖入 PDF，或选择本机文件</h3><p>支持文字版与扫描版英文 PDF<br /><span>最大 10 MB · 100 页 · 200,000 字</span></p>
      <input ref={input} type="file" accept="application/pdf,.pdf" className="visually-hidden" aria-label="上传英文 PDF" disabled={busy} onChange={event => { const value = event.target.files?.[0]; if (!busy && value) onChoose(value); event.target.value = ''; }} />
      <button type="button" className="button secondary" disabled={busy} onClick={() => input.current?.click()}><Upload size={16} aria-hidden="true" />{file ? '重新选择 PDF' : '选择 PDF 文件'}</button>
    </div>
    {file && <div className="pdf-selected-file" role="status"><FileText size={18} /><div><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(2)} MB · 导入失败可保留文件重试</span></div></div>}
    <fieldset className="pdf-recognition-modes" disabled={busy} aria-describedby={`${modeGroup}-limits`}>
      <legend>识别方式 <span>选一种适合这份材料的方式</span></legend>
      <div className="pdf-recognition-options">
        {recognitionOptions.map(option => <label key={option.value} className={`pdf-recognition-option${mode === option.value ? ' selected' : ''}`}>
          <input type="radio" name={`${modeGroup}-recognition`} value={option.value} checked={mode === option.value} onChange={() => { if (!busy) onModeChange(option.value); }} />
          <span className="pdf-recognition-option-copy"><strong>{option.title}{option.value === 'auto' && <span className="pdf-auto-badge">推荐</span>}</strong><span>{option.description}</span></span>
        </label>)}
      </div>
    </fieldset>
    <p className="pdf-ocr-limit" id={`${modeGroup}-limits`}><ScanText size={15} aria-hidden="true" /><span>每次最多识别 20 页扫描图像；纯文字 PDF 最多 100 页。OCR 不消耗 Agnes 额度。</span></p>
    <p className="pdf-import-note">阅读时显示正文，不保留原 PDF 排版。扫描文字可能有误，请在学习前核对；原文件可在阅读页下载。</p>
    {processing && <div className="pdf-recognition-progress" role="status" aria-live="polite"><LoaderCircle size={20} className="spin" aria-hidden="true" /><div><strong>{mode === 'text' ? '正在提取 PDF 正文' : '正在逐页读取 PDF'}</strong><p>{mode === 'text' ? '请稍候，等待期间请勿重复提交。' : '扫描页逐页识别，首次需加载 OCR。等待期间请勿重复提交。'}</p></div></div>}
    <button type="button" className="button primary pdf-import-submit" disabled={busy || !file} onClick={onSubmit}>{processing ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <FileText size={16} aria-hidden="true" />}{processing ? '正在导入 PDF…' : '导入 PDF'}</button>
  </div>;
}
