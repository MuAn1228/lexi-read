import { useCallback, useEffect, useRef } from 'react';
import type { FocusEvent, PointerEvent, RefObject } from 'react';
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

/** A steady navigation rail; only the icons respond to pointer proximity. */
export default function StudyDock({ items, view, disabled, onNavigate, vocabularyCount, navigationRef }: StudyDockProps) {
  const dockRef = useRef<HTMLElement | null>(null);
  const frameRef = useRef<number | null>(null);
  const pointerXRef = useRef<number | null>(null);

  const attachDock = useCallback((element: HTMLElement | null) => {
    dockRef.current = element;
    if (navigationRef) navigationRef.current = element;
  }, [navigationRef]);

  const clearProximity = useCallback(() => {
    if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    pointerXRef.current = null;
    dockRef.current?.querySelectorAll<HTMLButtonElement>('.study-dock__item').forEach(button => {
      button.style.removeProperty('--dock-proximity');
    });
  }, []);

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
    pointerXRef.current = event.clientX;
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      const pointerX = pointerXRef.current;
      if (pointerX === null) return;
      const buttons = dockRef.current?.querySelectorAll<HTMLButtonElement>('.study-dock__item');
      if (!buttons) return;
      // Read all geometry before updating styles. No React render on pointer movement.
      const weights = Array.from(buttons, button => {
        const bounds = button.getBoundingClientRect();
        const proximity = Math.max(0, 1 - Math.abs(pointerX - bounds.left - bounds.width / 2) / 126);
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
    const bounds = dock.getBoundingClientRect();
    const item = button.getBoundingClientRect();
    // Scroll only the rail; scrollIntoView would also move the reading page.
    if (item.left < bounds.left + 12) dock.scrollLeft += item.left - bounds.left - 12;
    else if (item.right > bounds.right - 12) dock.scrollLeft += item.right - bounds.right + 12;
  }

  const count = Math.max(0, Math.floor(vocabularyCount));
  const visibleCount = count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);

  return <nav
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
    {items.map(({ value, label, icon: Icon }) => <button
      key={value}
      type="button"
      className={`study-dock__item${view === value ? ' is-active' : ''}`}
      aria-label={label}
      aria-current={view === value ? 'page' : undefined}
      disabled={disabled}
      onClick={() => onNavigate(value)}
    >
      <span className="study-dock__icon" aria-hidden="true"><Icon size={23} strokeWidth={1.65} /></span>
      <span className="study-dock__label">{label}</span>
      {value === 'vocabulary' && count > 0 && <span
        className="study-dock__count"
        title={`${count.toLocaleString()} 个已标记单词`}
        aria-hidden="true"
      >{visibleCount}</span>}
    </button>)}
  </nav>;
}
