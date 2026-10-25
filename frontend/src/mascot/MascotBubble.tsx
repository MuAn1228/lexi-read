import { X } from 'lucide-react';
import type { CSSProperties } from 'react';

export function MascotBubble({ id, text, priority, x, y, width, dismiss }: {
  id: number; text: string; priority: number; x: number; y: number; width: number; dismiss: () => void;
}) {
  return <div key={id} className="mascot-bubble" data-testid="mascot-bubble" data-priority={priority} style={{ transform: `translate3d(${x}px, ${y}px, 0)`, width } as CSSProperties}>
    <span className="mascot-bubble-copy" role="status" aria-live="polite" aria-atomic="true">{text}</span>
    <button type="button" className="mascot-bubble-close" onClick={dismiss} aria-label="关闭 Lexi 提示"><X size={13} aria-hidden="true" /></button>
  </div>;
}
