import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import './VocabularyPagination.css';

type PageItem = number | 'before' | 'after';

/** Bound the page row while always keeping the current page and both ends visible. */
function pageItems(current: number, total: number): PageItem[] {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  if (current <= 4) return [1, 2, 3, 4, 5, 'after', total];
  if (current >= total - 3) return [1, 'before', total - 4, total - 3, total - 2, total - 1, total];
  return [1, 'before', current - 1, current, current + 1, 'after', total];
}

export default function VocabularyPagination({ currentPage, pageCount, onPageChange }: {
  currentPage: number; pageCount: number; onPageChange: (page: number) => void;
}) {
  const [jumpPage, setJumpPage] = useState(String(currentPage + 1));
  useEffect(() => setJumpPage(String(currentPage + 1)), [currentPage, pageCount]);
  const goTo = (page: number) => {
    if (Number.isInteger(page) && page >= 0 && page < pageCount && page !== currentPage) onPageChange(page);
  };

  return <div className="vocabulary-pagination">
    <nav aria-label="词库分页">
      <ul className="vocabulary-page-buttons">
        <li><button type="button" className="vocabulary-page-direction" aria-label="上一页" disabled={currentPage === 0} onClick={() => goTo(currentPage - 1)}><ChevronLeft size={16} aria-hidden="true" /><span>上一页</span></button></li>
        {pageItems(currentPage + 1, pageCount).map(item => <li key={item}>{typeof item === 'number'
          ? <button type="button" aria-label={`第 ${item} 页`} aria-current={item === currentPage + 1 ? 'page' : undefined} title={item === 1 ? '首页' : item === pageCount ? '末页' : undefined} onClick={() => goTo(item - 1)}>{item}</button>
          : <span className="vocabulary-page-gap" aria-hidden="true">…</span>}</li>)}
        <li><button type="button" className="vocabulary-page-direction" aria-label="下一页" disabled={currentPage + 1 >= pageCount} onClick={() => goTo(currentPage + 1)}><span>下一页</span><ChevronRight size={16} aria-hidden="true" /></button></li>
      </ul>
    </nav>
    {pageCount > 1 && <form className="vocabulary-page-jump" aria-label="跳转词库页码" onSubmit={event => { event.preventDefault(); if (jumpPage.trim()) goTo(Number(jumpPage) - 1); }}>
      <label><span>跳至</span><input type="number" inputMode="numeric" aria-label="跳转页码" min={1} max={pageCount} step={1} required value={jumpPage} onChange={event => setJumpPage(event.target.value)} /><span>页</span></label>
      <button type="submit">跳转</button>
    </form>}
  </div>;
}
