import { X } from 'lucide-react';
import { useLayoutEffect, useRef } from 'react';
import type { CSSProperties } from 'react';
import type { MascotSize } from './mascotPosition';
import type { MascotBubbleSide } from './mascotBubblePosition';

export function MascotBubble({ id, text, priority, x, y, width, maxHeight, side, tailOffset, dismiss }: {
  id: number; text: string; priority: number; x: number; y: number; width: number; maxHeight: number;
  side: MascotBubbleSide; tailOffset: number; dismiss: () => void;
}) {
  return <div key={id} className="mascot-bubble" data-testid="mascot-bubble" data-priority={priority} data-side={side} style={{ transform: `translate3d(${x}px, ${y}px, 0)`, width, '--mascot-bubble-max-height': `${maxHeight}px`, '--mascot-tail-offset': `${tailOffset}px` } as CSSProperties}>
    <span className="mascot-bubble-copy" role="status" aria-live="polite" aria-atomic="true">{text}</span>
    <button type="button" className="mascot-bubble-close" onClick={dismiss} aria-label="关闭 Lexi 提示"><X size={13} aria-hidden="true" /></button>
  </div>;
}

export type MascotBubbleMeasurements = { text: string; widths: string; maxHeight: number; sizes: MascotSize[] };

/** Measure the same wrapping and padding before painting; never announce the hidden copies. */
export function MascotBubbleMeasure({ text, widths, maxHeight, onMeasure }: {
  text: string; widths: number[]; maxHeight: number; onMeasure: (value: MascotBubbleMeasurements) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const key = widths.join(',');
  useLayoutEffect(() => {
    const nodes = Array.from(root.current?.children ?? []);
    const measure = () => onMeasure({ text, widths: key, maxHeight, sizes: nodes.map(node => {
      const rect = node.getBoundingClientRect();
      return { width: rect.width, height: rect.height };
    }) });
    measure();
    const observer = new ResizeObserver(measure);
    nodes.forEach(node => observer.observe(node));
    return () => observer.disconnect();
  }, [text, key, maxHeight, onMeasure]);
  return <div ref={root} className="mascot-bubble-measure" aria-hidden="true" inert>{widths.map(width =>
    <div key={width} className="mascot-bubble" style={{ width, '--mascot-bubble-max-height': `${maxHeight}px` } as CSSProperties}><span className="mascot-bubble-copy">{text}</span></div>
  )}</div>;
}
