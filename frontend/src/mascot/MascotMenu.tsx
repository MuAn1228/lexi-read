import { useEffect, useRef } from 'react';
import { EyeOff, RotateCcw, X } from 'lucide-react';

export function MascotMenu({ x, y, maxHeight, scale, idleBubbleEnabled, onScale, onIdle, onReset, onHide, onClose }: {
  x: number; y: number; maxHeight: number; scale: number; idleBubbleEnabled: boolean; onScale: (scale: number) => void;
  onIdle: (enabled: boolean) => void; onReset: () => void; onHide: () => void; onClose: () => void;
}) {
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Focus changes only after the user explicitly opens settings, never for a bubble.
    menu.current?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true });
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !menu.current?.contains(event.target)) onClose(); };
    document.addEventListener('pointerdown', outside, true);
    return () => document.removeEventListener('pointerdown', outside, true);
  }, [onClose]);
  return <div ref={menu} className="mascot-menu" data-testid="mascot-menu" role="group" aria-label="Lexi 助手设置" style={{ transform: `translate3d(${x}px, ${y}px, 0)`, maxHeight }} onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape') onClose(); }} onKeyUp={event => event.stopPropagation()}>
    <div className="mascot-menu-heading"><span>Lexi · 阅读助手</span><button type="button" aria-label="关闭助手设置" onClick={onClose}><X size={14} /></button></div>
    <fieldset><legend>角色大小</legend><div className="mascot-size-options">{[{ scale: .9, label: '小' }, { scale: 1, label: '中' }, { scale: 1.2, label: '大' }].map(option => <button key={option.scale} type="button" aria-pressed={Math.abs(scale - option.scale) < .05} onClick={() => onScale(option.scale)}>{option.label}</button>)}</div></fieldset>
    <label className="mascot-idle-setting"><input type="checkbox" checked={idleBubbleEnabled} onChange={event => onIdle(event.target.checked)} /><span>点击时显示日常台词</span></label>
    <button type="button" className="mascot-menu-action" onClick={() => { onReset(); onClose(); }}><RotateCcw size={14} />恢复默认位置</button>
    <button type="button" className="mascot-menu-action" onClick={onHide}><EyeOff size={14} />隐藏助手</button>
    <p>可从页面上方的「阅读助手」重新显示。</p>
  </div>;
}
