import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { FocusEvent, PointerEvent, RefObject } from 'react';
import { createPortal } from 'react-dom';
import { Menu, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { View } from '../types';
import './StudyDock.css';

type StudyDockProps = {
  items: readonly { value: View; label: string; icon: LucideIcon }[];
  view: View;
  disabled: boolean;
  onNavigate: (view: View) => void;
  vocabularyCount: number;
  navigationRef?: RefObject<HTMLElement | null>;
};

const pointerMotionQuery = '(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)';
const mobileQuery = '(max-width: 799px)';

/** A left navigation rail, presented as a modal menu on narrow screens. */
export default function StudyDock({ items, view, disabled, onNavigate, vocabularyCount, navigationRef }: StudyDockProps) {
  const dockRef = useRef<HTMLElement | null>(null);
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const frameRef = useRef<number | null>(null);
  const pointerYRef = useRef<number | null>(null);
  const returnToTrigger = useRef(true);
  const [mobile, setMobile] = useState(() => window.matchMedia(mobileQuery).matches);
  const [menuOpen, setMenuOpen] = useState(false);
  const drawerId = useId();
  const titleId = useId();

  const attachDock = useCallback((element: HTMLElement | null) => {
    dockRef.current = element;
    if (navigationRef) navigationRef.current = element;
  }, [navigationRef]);

  const clearProximity = useCallback(() => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    pointerYRef.current = null;
    dockRef.current?.querySelectorAll<HTMLButtonElement>('.study-dock__item').forEach(button => {
      button.style.removeProperty('--dock-proximity');
    });
  }, []);

  const closeMenu = useCallback((restoreFocus = true) => {
    returnToTrigger.current = restoreFocus;
    // Close before navigation, letting App move focus to the destination h1.
    dialogRef.current?.close();
    setMenuOpen(false);
  }, []);

  useEffect(() => {
    const media = window.matchMedia(mobileQuery);
    const change = () => {
      if (!media.matches) closeMenu(false);
      setMobile(media.matches);
      clearProximity();
    };
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, [clearProximity, closeMenu]);

  useEffect(() => { if (disabled) closeMenu(); }, [disabled, closeMenu]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!mobile || !menuOpen || !dialog) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Native modal isolation leaves our own nav interactive without custom inert.
    dialog.showModal();
    const selected = dialog.querySelector<HTMLButtonElement>('[aria-current="page"]:not(:disabled)');
    (selected ?? dialog.querySelector<HTMLButtonElement>('button'))?.focus({ preventScroll: true });
    return () => {
      if (dialog.open) dialog.close();
      document.body.style.overflow = previousOverflow;
      if (returnToTrigger.current && triggerRef.current?.getClientRects().length) triggerRef.current.focus({ preventScroll: true });
    };
  }, [mobile, menuOpen]);

  useEffect(() => {
    const dock = dockRef.current;
    const button = dock?.querySelector<HTMLButtonElement>('[aria-current="page"]');
    if (dock && button) revealButton(dock, button);
  }, [view, mobile, menuOpen, items.length]);

  useEffect(() => {
    clearProximity();
    const media = window.matchMedia(pointerMotionQuery);
    media.addEventListener('change', clearProximity);
    window.addEventListener('blur', clearProximity);
    window.addEventListener('resize', clearProximity);
    return () => {
      clearProximity();
      media.removeEventListener('change', clearProximity);
      window.removeEventListener('blur', clearProximity);
      window.removeEventListener('resize', clearProximity);
    };
  }, [clearProximity, disabled, items]);

  function onPointerMove(event: PointerEvent<HTMLElement>) {
    if (disabled || event.pointerType !== 'mouse' || !window.matchMedia(pointerMotionQuery).matches) {
      clearProximity();
      return;
    }
    pointerYRef.current = event.clientY;
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      const pointerY = pointerYRef.current;
      if (pointerY === null) return;
      const buttons = dockRef.current?.querySelectorAll<HTMLButtonElement>('.study-dock__item');
      if (!buttons) return;
      // Read all geometry before updating styles. No React render on pointer movement.
      const weights = Array.from(buttons, button => {
        const bounds = button.getBoundingClientRect();
        const proximity = Math.max(0, 1 - Math.abs(pointerY - bounds.top - bounds.height / 2) / 90);
        return proximity * proximity;
      });
      buttons.forEach((button, index) => button.style.setProperty('--dock-proximity', weights[index].toFixed(3)));
    });
  }

  function onFocus(event: FocusEvent<HTMLElement>) {
    clearProximity();
    const dock = dockRef.current;
    const button = event.target.closest<HTMLButtonElement>('.study-dock__item');
    if (!dock || !button || !dock.contains(button)) return;
    revealButton(dock, button);
  }

  const count = Math.max(0, Math.floor(vocabularyCount));
  const visibleCount = count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);

  const rail = <nav
    ref={attachDock}
    className="study-dock"
    aria-label="主要导航"
    aria-busy={disabled || undefined}
    onPointerMove={onPointerMove}
    onPointerLeave={clearProximity}
    onPointerCancel={clearProximity}
    onFocusCapture={onFocus}
    onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) clearProximity(); }}
    onScroll={clearProximity}
  >
    <div className="study-dock__caption" aria-hidden="true"><span>学习导航</span><span>{String(items.length).padStart(2, '0')}</span></div>
    {items.map(({ value, label, icon: Icon }) => <button
      key={value}
      type="button"
      className={`study-dock__item${view === value ? ' is-active' : ''}`}
      aria-label={label}
      aria-current={view === value ? 'page' : undefined}
      disabled={disabled}
      onClick={() => { if (mobile) closeMenu(value === view); onNavigate(value); }}
    >
      <span className="study-dock__icon" aria-hidden="true"><Icon size={21} strokeWidth={1.65} /></span>
      <span className="study-dock__label">{label}</span>
      {value === 'vocabulary' && count > 0 && <span
        className="study-dock__count"
        title={`${count.toLocaleString()} 个已标记单词`}
        aria-hidden="true"
      >{visibleCount}</span>}
    </button>)}
  </nav>;

  if (!mobile) return rail;

  return <>
    <button ref={triggerRef} type="button" className="study-menu-trigger" aria-label="打开导航菜单" aria-haspopup="dialog" aria-expanded={menuOpen} aria-controls={drawerId} disabled={disabled} onClick={() => {
      // Import, dictionary and OCR dialogs own the modal layer while open.
      if (document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) return;
      returnToTrigger.current = true;
      setMenuOpen(true);
    }}><Menu size={22} aria-hidden="true" /></button>
    {menuOpen && createPortal(<dialog ref={dialogRef} id={drawerId} className="study-drawer" aria-labelledby={titleId} aria-modal="true"
      onCancel={event => { event.preventDefault(); closeMenu(); }}
      onClose={() => setMenuOpen(false)}
      onKeyDown={event => {
        if (event.key !== 'Tab') return;
        const elements = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
        const first = elements[0];
        const last = elements[elements.length - 1];
        const active = document.activeElement;
        if (!first) { event.preventDefault(); event.currentTarget.focus(); }
        else if (event.shiftKey && (active === first || active === event.currentTarget || !event.currentTarget.contains(active))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (active === last || active === event.currentTarget || !event.currentTarget.contains(active))) { event.preventDefault(); first.focus(); }
      }}
      onClick={event => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeMenu();
      }}>
      <div className="study-drawer__heading"><h2 id={titleId}>学习导航</h2><button type="button" className="study-drawer__close" aria-label="关闭导航菜单" onClick={() => closeMenu()}><X size={21} aria-hidden="true" /></button></div>
      {rail}
    </dialog>, document.body)}
  </>;
}

function revealButton(dock: HTMLElement, button: HTMLButtonElement) {
  const bounds = dock.getBoundingClientRect();
  const item = button.getBoundingClientRect();
  // Scroll only the list, preserving the reading and vocabulary browsing position.
  if (item.top < bounds.top + 8) dock.scrollTop += item.top - bounds.top - 8;
  else if (item.bottom > bounds.bottom - 8) dock.scrollTop += item.bottom - bounds.bottom + 8;
}
