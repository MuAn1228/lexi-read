import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Sparkles, X } from 'lucide-react';
import type { ReadingDocument, Vocabulary } from '../types';
import WordPlayground from './WordPlayground';
import './InspirationCorner.css';

type InspirationContent = { vocabulary?: Vocabulary[]; document?: ReadingDocument | null };

function InspirationDialog({ opener, onClose, vocabulary, document: readingDocument }: InspirationContent & { opener: HTMLElement; onClose: () => void }) {
  const dialog = useRef<HTMLElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => {
    const modal = dialog.current;
    if (!modal) return;
    const shell = document.querySelector<HTMLElement>('.app-shell');
    const previousInert = shell?.inert ?? false;
    const previousOverflow = document.body.style.overflow;
    if (shell) shell.inert = true;
    document.body.style.overflow = 'hidden';
    closeButton.current?.focus();
    const focusable = () => Array.from(modal.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
    const key = (event: KeyboardEvent) => {
      // A dictionary opens in the native top layer; only the top dialog owns keys.
      if (document.querySelector('dialog[data-dictionary-dialog][open]')) return;
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); return; }
      if (event.key !== 'Tab') return;
      const elements = focusable(); const first = elements[0]; const last = elements[elements.length - 1];
      if (!first) { event.preventDefault(); modal.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === modal)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === modal)) { event.preventDefault(); first.focus(); }
    };
    const focus = (event: FocusEvent) => { if (!document.querySelector('dialog[data-dictionary-dialog][open]') && !modal.contains(event.target as Node)) (focusable()[0] ?? modal).focus(); };
    document.addEventListener('keydown', key, true);
    document.addEventListener('focusin', focus);
    return () => {
      document.removeEventListener('keydown', key, true);
      document.removeEventListener('focusin', focus);
      document.body.style.overflow = previousOverflow;
      if (shell) shell.inert = previousInert;
      if (opener.isConnected && !opener.matches(':disabled')) opener.focus();
      else document.querySelector<HTMLElement>('h1')?.focus();
    };
  }, [opener, onClose]);
  return createPortal(<div className="inspiration-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="inspiration-dialog" ref={dialog} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} tabIndex={-1}>
      <header className="inspiration-dialog__heading"><div><p>TAKE A LITTLE BREATH</p><h2 id={titleId}>给好奇心一点灵感。</h2></div><button ref={closeButton} type="button" className="icon-button" onClick={onClose} aria-label="关闭灵感词卡"><X size={21} /></button></header>
      <p id={descriptionId} className="inspiration-dialog__description">翻一张词卡，或从自己的词库找一点灵感。</p>
      <WordPlayground vocabulary={vocabulary} document={readingDocument} />
      <p className="inspiration-dialog__note">翻卡不会改变词库状态和复习进度。</p>
    </section>
  </div>, document.body);
}

/** A voluntary break, separate from the user's learning state and drafts. */
export default function InspirationCorner({ disabled, vocabulary, document }: InspirationContent & { disabled: boolean }) {
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  const close = useCallback(() => setOpener(null), []);
  return <>
    <button type="button" className="button inspiration-launcher" disabled={disabled} aria-haspopup="dialog" aria-expanded={opener !== null}
      onClick={event => setOpener(event.currentTarget)} data-motion-burst="">
      <span data-motion-magnet="" aria-hidden="true"><Sparkles size={16} /></span>灵感一下
    </button>
    {opener && <InspirationDialog opener={opener} onClose={close} vocabulary={vocabulary} document={document} />}
  </>;
}
