import { FileText, LoaderCircle, Upload } from 'lucide-react';
import { useRef, useState } from 'react';

export default function PdfImportForm({ file, disabled, onChoose, onSubmit }: {
  file: File | null; disabled: boolean; onChoose: (file: File) => void; onSubmit: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  return <div className="pdf-import-form">
    <p className="text-import-intro">把教材、试卷或论文带进阅读空间。按 PDF 页码提取正文，点击单词即可学习。</p>
    <div className={`upload-zone pdf-upload-zone ${dragOver ? 'drag-over' : ''}`} onDragOver={event => { event.preventDefault(); setDragOver(true); }} onDragLeave={() => setDragOver(false)} onDrop={event => { event.preventDefault(); setDragOver(false); if (!disabled && event.dataTransfer.files[0]) onChoose(event.dataTransfer.files[0]); }}>
      <div className="upload-icon"><FileText size={25} /></div><h3>拖入 PDF，或选择本机文件</h3><p>可选中文字的英文 PDF<br /><span>最大 10 MB · 100 页 · 200,000 字</span></p>
      <input ref={input} type="file" accept="application/pdf,.pdf" className="visually-hidden" aria-label="上传英文 PDF" disabled={disabled} onChange={event => { const value = event.target.files?.[0]; if (value) onChoose(value); event.target.value = ''; }} />
      <button type="button" className="button secondary" disabled={disabled} onClick={() => input.current?.click()}><Upload size={16} />{file ? '重新选择 PDF' : '选择 PDF 文件'}</button>
    </div>
    {file && <div className="pdf-selected-file" role="status"><FileText size={18} /><div><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(2)} MB · 导入失败可保留文件重试</span></div></div>}
    <p className="pdf-import-note">阅读时显示提取的正文，不保留原 PDF 排版。扫描件和图片 PDF 暂不支持；原文件可在阅读页下载。</p>
    <button type="button" className="button primary pdf-import-submit" disabled={disabled || !file} onClick={onSubmit}>{disabled ? <LoaderCircle size={16} className="spin" /> : <FileText size={16} />}导入 PDF</button>
  </div>;
}
