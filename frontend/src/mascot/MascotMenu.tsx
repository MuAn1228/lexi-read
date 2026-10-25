import { useEffect, useRef } from 'react';
import { EyeOff, MessageCircle, RotateCcw, X } from 'lucide-react';
import { MASCOT_GESTURES } from './mascotInteraction';
import type { MascotGestureKind } from './mascotTypes';

export function MascotMenu({ x, y, maxHeight, scale, idleBubbleEnabled, interactionEnabled, interactionBlocked, onAssistant, onSpeak, onScale, onIdle, onInteraction, onGesture, onReset, onHide, onClose }: {
  x: number; y: number; maxHeight: number; scale: number; idleBubbleEnabled: boolean;
  interactionEnabled: boolean; interactionBlocked: boolean; onGesture: (kind: MascotGestureKind) => void;
  onScale: (scale: number) => void; onIdle: (enabled: boolean) => void; onInteraction: (enabled: boolean) => void;
  onReset: () => void; onHide: () => void; onClose: () => void;
  onAssistant: () => void;
  onSpeak: () => void;
}) {
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Focus changes only after the user explicitly opens settings, never for a bubble.
    menu.current?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true });
    const outside = (event: PointerEvent) => {
      if (!(event.target instanceof Node) || menu.current?.contains(event.target)) return;
      if (event.target instanceof Element && event.target.closest('[data-testid="mascot-menu-toggle"]')) return;
      onClose();
    };
    // A busy task may disable the focused interaction button and return focus to body.
    // Keep review shortcuts isolated for the whole lifetime of this explicitly opened menu.
    const guardReviewKeys = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !['1', '2', '3', 'ArrowDown'].includes(event.key)) return;
      if (event.target instanceof Element && event.target.closest('input,textarea,select,[contenteditable="true"],[contenteditable=""]')) return;
      event.preventDefault();
      event.stopPropagation();
    };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', guardReviewKeys, true);
    return () => {
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('keydown', guardReviewKeys, true);
    };
  }, [onClose]);
  return <div ref={menu} className="mascot-menu" data-testid="mascot-menu" role="group" aria-label="Lexi 助手设置" style={{ transform: `translate3d(${x}px, ${y}px, 0)`, maxHeight }} onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape') onClose(); }} onKeyUp={event => event.stopPropagation()}>
    <div className="mascot-menu-heading"><span>Lexi · 阅读助手</span><button type="button" aria-label="关闭助手设置" onClick={onClose}><X size={14} /></button></div>
    <button type="button" className="mascot-menu-assistant" onClick={onAssistant}><MessageCircle size={16} /><span>和 Lexi 一起学<small>Agnes · 讲解、记忆、随堂练习</small></span></button>
    <button type="button" className="mascot-menu-speak" data-testid="mascot-speak" disabled={interactionBlocked} onClick={onSpeak}><MessageCircle size={14} /><span>说句话</span></button>
    <section className="mascot-interaction-settings" aria-label="角色互动">
      <label className="mascot-interaction-setting"><input type="checkbox" data-testid="mascot-interaction-toggle" checked={interactionEnabled} onChange={event => onInteraction(event.target.checked)} /><span>角色动作与表情</span></label>
      <fieldset><legend>和 Lexi 互动</legend><div className="mascot-gesture-options">
        {MASCOT_GESTURES.map(gesture => <button key={gesture.kind} type="button" data-testid={`mascot-gesture-${gesture.kind}`} disabled={!interactionEnabled || interactionBlocked} onClick={() => onGesture(gesture.kind)}><span>{gesture.label}</span><small>{gesture.description}</small></button>)}
      </div></fieldset>
      <div className="mascot-interaction-hint">{interactionBlocked ? '正在处理学习任务，互动会稍后恢复。' : !interactionEnabled ? '已关闭动作与表情，学习提示仍然保留。' : '单击角色会回应你，连续点击两次摸摸头。'}</div>
    </section>
    <fieldset><legend>角色大小</legend><div className="mascot-size-options">{[{ scale: .9, label: '小' }, { scale: 1, label: '中' }, { scale: 1.2, label: '大' }].map(option => <button key={option.scale} type="button" aria-pressed={Math.abs(scale - option.scale) < .05} onClick={() => onScale(option.scale)}>{option.label}</button>)}</div></fieldset>
    <label className="mascot-idle-setting"><input type="checkbox" checked={idleBubbleEnabled} onChange={event => onIdle(event.target.checked)} /><span>点击时显示日常台词</span></label>
    <button type="button" className="mascot-menu-action" onClick={() => { onReset(); onClose(); }}><RotateCcw size={14} />恢复默认位置</button>
    <button type="button" className="mascot-menu-action" onClick={onHide}><EyeOff size={14} />隐藏助手</button>
    <p>可从页面上方的「阅读助手」重新显示。</p>
  </div>;
}
