import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

type ReadingPosition = { id: string | null; offset: number; fraction: number };

/** Keep a visible word anchored when the reading column or type size changes. */
function capture(surface: HTMLElement | null): ReadingPosition | null {
  if (!surface) return null;
  const top = surface.getBoundingClientRect().top;
  const word = Array.from(surface.querySelectorAll<HTMLElement>('[data-occurrence-id]'))
    .find(element => element.getBoundingClientRect().top >= top);
  return { id: word?.dataset.occurrenceId ?? null, offset: word ? word.getBoundingClientRect().top - top : 0,
    fraction: surface.scrollTop / Math.max(1, surface.scrollHeight - surface.clientHeight) };
}

export function useFocusReading(available: boolean, documentId: number | null) {
  const [activeDocument, setActiveDocument] = useState<number | null>(null);
  const active = available && documentId !== null && activeDocument === documentId;
  const [lookupOpen, setLookupOpen] = useState(false);
  const [fontSize, setFontSize] = useState(24);
  const workspace = useRef<HTMLDivElement>(null);
  const entryButton = useRef<HTMLButtonElement>(null);
  const exitButton = useRef<HTMLButtonElement>(null);
  const lookupClose = useRef<HTMLButtonElement>(null);
  const lookupOpener = useRef<HTMLElement | null>(null);
  const pageScroll = useRef(0);
  const pending = useRef<ReadingPosition | null>(null);
  const surface = () => workspace.current?.querySelector<HTMLElement>('.text-article, .document-canvas') ?? null;

  const toggle = useCallback(() => {
    pending.current = capture(surface());
    if (!active) pageScroll.current = window.scrollY;
    setLookupOpen(false);
    setActiveDocument(active ? null : documentId);
  }, [active, documentId]);
  const changeFont = (delta: number) => {
    pending.current = capture(surface());
    setFontSize(size => Math.max(18, Math.min(32, size + delta)));
  };
  const openLookup = () => {
    if (!active) return;
    lookupOpener.current = window.document.activeElement as HTMLElement | null;
    setLookupOpen(true);
  };
  const closeLookup = useCallback(() => {
    setLookupOpen(false);
    const opener = lookupOpener.current;
    if (opener?.isConnected && !opener.matches(':disabled')) opener.focus({ preventScroll: true });
    else exitButton.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    if (!available || activeDocument !== documentId) { setActiveDocument(null); setLookupOpen(false); pending.current = null; }
  }, [available, documentId, activeDocument]);
  useLayoutEffect(() => {
    const element = surface(); const saved = pending.current;
    if (element && saved) {
      const word = saved.id ? element.querySelector<HTMLElement>(`[data-occurrence-id="${saved.id}"]`) : null;
      if (word) element.scrollTop += word.getBoundingClientRect().top - element.getBoundingClientRect().top - saved.offset;
      else element.scrollTop = saved.fraction * Math.max(0, element.scrollHeight - element.clientHeight);
      pending.current = null;
    }
  }, [active, fontSize]);
  useLayoutEffect(() => {
    if (!active) return;
    const previous = window.document.body.style.overflow;
    window.document.body.style.overflow = 'hidden';
    exitButton.current?.focus({ preventScroll: true });
    return () => {
      window.document.body.style.overflow = previous;
      window.scrollTo({ top: pageScroll.current, behavior: 'instant' });
      entryButton.current?.focus({ preventScroll: true });
    };
  }, [active]);
  useEffect(() => {
    if (active && lookupOpen) lookupClose.current?.focus({ preventScroll: true });
  }, [active, lookupOpen]);
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || window.document.querySelector('[aria-modal="true"], dialog[open]')) return;
      event.preventDefault();
      if (lookupOpen) closeLookup(); else toggle();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [active, lookupOpen, closeLookup, toggle]);
  return { active, toggle, fontSize, changeFont, lookupOpen, openLookup, closeLookup, workspace, entryButton, exitButton, lookupClose };
}
