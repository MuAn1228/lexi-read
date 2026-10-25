import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react';
import { createPortal } from 'react-dom';
import { MoreHorizontal } from 'lucide-react';
import { useMascot, useMascotSnapshot } from './useMascot';
import { MascotCharacter } from './MascotCharacter';
import { MascotBubble, MascotBubbleMeasure } from './MascotBubble';
import type { MascotBubbleMeasurements } from './MascotBubble';
import { findMascotBubblePlacement } from './mascotBubblePosition';
import { MascotMenu } from './MascotMenu';
import { clampMascot, snapMascot } from './mascotPosition';
import { resolveMascotDock } from './mascotRestingPosition';
import type { MascotPoint, MascotRect, MascotSize, MascotViewport } from './mascotPosition';

type Layout = { viewport: MascotViewport; obstacles: MascotRect[]; controls: MascotRect[]; navigation: MascotRect[]; sidebarRight: number; modal: boolean };
type Drag = { id: number; origin: MascotPoint; pointer: MascotPoint; current: MascotPoint; moved: boolean; longPressed: boolean; size: MascotSize };
const initialViewport: MascotViewport = { x: 0, y: 0, width: 1440, height: 900 };
const SURFACES = '.document-panel,.word-list,.context-panel,.vocabulary-table,.vocabulary-list,.article-text,.text-article,.image-stage,.relation-workspace,.reading-bar,.reading-library-tools,.page-heading-copy,.heading-actions,.review-summary,.today-review-heading>div,.review-stats,.today-review-footer>span';
const CONTROLS = 'main button,main input,main select,main textarea,main a,.toast,.study-dock,.study-menu-trigger,.topbar,.vocabulary-batch-toolbar';

function readRects(selector: string, viewport: MascotViewport): MascotRect[] {
  const result: MascotRect[] = [];
  document.querySelectorAll<HTMLElement>(selector).forEach(element => {
    // aria-hidden may still be painted (the toast uses a separate live announcer).
    if (element.closest('[data-mascot-root], [inert], [hidden]')) return;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.bottom < viewport.y || rect.top > viewport.y + viewport.height || rect.right < viewport.x || rect.left > viewport.x + viewport.width) return;
    const style = getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') return;
    result.push({ x: rect.left, y: rect.top, width: rect.width, height: rect.height });
  });
  return result;
}

function useLayoutMeasurements(): Layout {
  const [layout, setLayout] = useState<Layout>({ viewport: initialViewport, obstacles: [], controls: [], navigation: [], sidebarRight: 0, modal: false });
  useEffect(() => {
    let frame = 0;
    const safeArea = document.createElement('div');
    safeArea.className = 'mascot-safe-area-probe';
    safeArea.setAttribute('aria-hidden', 'true');
    safeArea.dataset.mascotRoot = 'true';
    document.body.append(safeArea);
    const measure = () => {
      frame = 0;
      const visual = window.visualViewport;
      const safe = getComputedStyle(safeArea);
      const left = parseFloat(safe.paddingLeft) || 0;
      const right = parseFloat(safe.paddingRight) || 0;
      const top = parseFloat(safe.paddingTop) || 0;
      const bottom = parseFloat(safe.paddingBottom) || 0;
      const viewport = { x: (visual?.offsetLeft ?? 0) + left, y: (visual?.offsetTop ?? 0) + top, width: Math.max(1, (visual?.width ?? window.innerWidth) - left - right), height: Math.max(1, (visual?.height ?? window.innerHeight) - top - bottom) };
      const controls = readRects(CONTROLS, viewport);
      const modal = Array.from(document.querySelectorAll<HTMLElement>('dialog[open],[aria-modal="true"]')).some(element => {
        if (element.closest('[data-mascot-root]')) return false;
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && getComputedStyle(element).visibility !== 'hidden';
      });
      const shell = document.querySelector<HTMLElement>('.app-shell');
      const sidebarWidth = shell ? parseFloat(getComputedStyle(shell).paddingLeft) || 0 : 0;
      const sidebarRight = sidebarWidth > 0 ? (shell?.getBoundingClientRect().left ?? 0) + sidebarWidth : viewport.x;
      setLayout({ viewport, controls, obstacles: [...readRects(SURFACES, viewport), ...controls], navigation: readRects('.study-dock,.study-menu-trigger,.topbar', viewport), sidebarRight, modal });
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    const resize = new ResizeObserver(schedule);
    const main = document.querySelector('main');
    resize.observe(document.documentElement);
    if (main) resize.observe(main);
    // Observe application changes only. The portal's own animations never reschedule measurement.
    const changes = new MutationObserver(records => {
      if (records.some(record => !(record.target instanceof Element && record.target.closest('[data-mascot-root]')))) schedule();
    });
    const app = document.getElementById('root');
    if (app) changes.observe(app, { childList: true, subtree: true, attributes: true, attributeFilter: ['open', 'hidden', 'aria-modal', 'class', 'inert'] });
    changes.observe(document.body, { childList: true });
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', schedule, true);
    window.visualViewport?.addEventListener('resize', schedule);
    window.visualViewport?.addEventListener('scroll', schedule);
    document.addEventListener('toggle', schedule, true);
    schedule();
    return () => {
      cancelAnimationFrame(frame); resize.disconnect(); changes.disconnect();
      safeArea.remove();
      window.removeEventListener('resize', schedule); window.removeEventListener('scroll', schedule, true);
      window.visualViewport?.removeEventListener('resize', schedule); window.visualViewport?.removeEventListener('scroll', schedule);
      document.removeEventListener('toggle', schedule, true);
    };
  }, []);
  return layout;
}

export function MascotWidget() {
  const mascot = useMascot();
  const { state, reactionId, bubble, config, suppressed, gesture, interactionBlocked } = useMascotSnapshot();
  const layout = useLayoutMeasurements();
  const { viewport } = layout;
  const [menuOpen, setMenuOpen] = useState(false);
  const [pressed, setPressed] = useState(false);
  const [dragPosition, setDragPosition] = useState<MascotPoint | null>(null);
  const [dragging, setDragging] = useState(false);
  const [snapping, setSnapping] = useState(false);
  const [dragSize, setDragSize] = useState<MascotSize | null>(null);
  const [bubbleMeasurements, setBubbleMeasurements] = useState<MascotBubbleMeasurements | null>(null);
  const measureBubble = useCallback((value: MascotBubbleMeasurements) => setBubbleMeasurements(previous =>
    previous?.text === value.text && previous.widths === value.widths && previous.maxHeight === value.maxHeight
      && previous.sizes.every((size, i) => size.width === value.sizes[i]?.width && size.height === value.sizes[i]?.height)
      ? previous : value), []);
  const drag = useRef<Drag | null>(null);
  const longPress = useRef<ReturnType<typeof setTimeout> | null>(null);
  const snapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const characterButton = useRef<HTMLButtonElement>(null);
  const visible = !config.hidden && !suppressed && !layout.modal;
  const mobile = viewport.width <= 620;
  const width = Math.min(viewport.width * .3, mobile ? Math.max(54, Math.min(90, 72 * config.scale)) : Math.max(72, Math.min(120, 96 * config.scale)));
  const fullSize = { width, height: Math.round(width * 1.18) };
  const defaultPoint = { x: viewport.x + viewport.width - fullSize.width - 24, y: viewport.y + viewport.height - fullSize.height - 24 };
  const preferred = { x: config.x ?? defaultPoint.x, y: config.y ?? defaultPoint.y };
  // Scrolling changes the page under this fixed dock, never its size or saved position.
  if (config.anchor === 'left') preferred.x = Math.max(viewport.x, layout.sidebarRight) + 24;
  if (config.anchor === 'right') preferred.x = defaultPoint.x;
  if (config.anchor === 'bottom') preferred.y = defaultPoint.y;
  const size = dragSize ?? fullSize;
  const position = dragPosition ?? resolveMascotDock(preferred, size, viewport, config.side, layout.sidebarRight);
  const bubbleWidths = [...new Set((mobile ? [220, 168, 148, 120] : [280, 220, 176, 148, 120]).map(value => Math.max(48, Math.min(value, viewport.width - 24))))];
  const bubbleMaxHeight = Math.max(48, Math.min(180, viewport.height - 24));
  const measuredSizes = bubble && bubbleMeasurements?.text === bubble.text && bubbleMeasurements.widths === bubbleWidths.join(',')
    && bubbleMeasurements.maxHeight === bubbleMaxHeight ? bubbleMeasurements.sizes : [];
  // Only the speech bubble adapts: try clear whitespace, then a floating callout above
  // non-interactive content. Neither pass is allowed to move or shrink the character.
  const bubbleLayouts = bubble && !menuOpen && !dragPosition ? [layout.obstacles, layout.controls].flatMap(obstacles => measuredSizes.map(bubbleSize => ({ bubbleSize, placement: findMascotBubblePlacement({
    character: position, characterSize: size, bubbleSize, viewport, obstacles, characterObstacles: layout.navigation, fixedCharacter: true, inset: 8,
  }) }))) : [];
  const bubbleLayout = bubbleLayouts.find(value => value.placement !== null);
  const actualSide = position.x + size.width / 2 < viewport.x + viewport.width / 2 ? 'left' : 'right';

  const closeMenu = useCallback(() => setMenuOpen(false), []);
  const clearLongPress = useCallback(() => { if (longPress.current) clearTimeout(longPress.current); longPress.current = null; }, []);
  const cancelDrag = useCallback(() => {
    clearLongPress();
    mascot.cancelGesture();
    if (snapTimer.current) clearTimeout(snapTimer.current);
    snapTimer.current = null; setSnapping(false);
    const pointerId = drag.current?.id;
    drag.current = null;
    if (pointerId !== undefined && characterButton.current?.hasPointerCapture(pointerId)) characterButton.current.releasePointerCapture(pointerId);
    setDragging(false); setPressed(false); setDragPosition(null); setDragSize(null);
  }, [clearLongPress, mascot]);
  useEffect(() => {
    const onVisibility = () => { if (document.hidden) cancelDrag(); };
    window.addEventListener('blur', cancelDrag);
    document.addEventListener('visibilitychange', onVisibility);
    return () => { window.removeEventListener('blur', cancelDrag); document.removeEventListener('visibilitychange', onVisibility); clearLongPress(); if (snapTimer.current) clearTimeout(snapTimer.current); };
  }, [cancelDrag, clearLongPress]);
  useEffect(() => { if (!visible) { cancelDrag(); setMenuOpen(false); } }, [visible, cancelDrag]);

  const pointerDown = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!event.isPrimary || event.button !== 0 || drag.current) return;
    event.preventDefault();
    closeMenu();
    if (snapTimer.current) clearTimeout(snapTimer.current);
    snapTimer.current = null; setSnapping(false);
    // A new grab can interrupt a snap. Start at the currently painted position,
    // rather than its CSS transition target, so the character never jumps.
    const painted = event.currentTarget.parentElement!.getBoundingClientRect();
    const origin = { x: painted.left, y: painted.top };
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { id: event.pointerId, origin, pointer: { x: event.clientX, y: event.clientY }, current: origin, moved: false, longPressed: false, size };
    setDragPosition(origin); setDragSize(size);
    setPressed(true);
    clearLongPress();
    longPress.current = setTimeout(() => {
      if (!drag.current || drag.current.moved) return;
      drag.current.longPressed = true;
      setPressed(false); setMenuOpen(true);
    }, 500);
  };
  const pointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const active = drag.current;
    if (!active || active.id !== event.pointerId || active.longPressed) return;
    const dx = event.clientX - active.pointer.x;
    const dy = event.clientY - active.pointer.y;
    if (!active.moved && Math.hypot(dx, dy) <= 4) return;
    if (!active.moved) { active.moved = true; clearLongPress(); mascot.cancelGesture(); setDragging(true); setPressed(false); setDragSize(active.size); }
    // No easing while captured: every pointer delta maps directly to the widget.
    active.current = { x: active.origin.x + dx, y: active.origin.y + dy };
    setDragPosition(active.current);
  };
  const pointerUp = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const active = drag.current;
    if (!active || active.id !== event.pointerId) return;
    clearLongPress();
    drag.current = null;
    setPressed(false); setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (active.moved) {
      const snapped = snapMascot(active.current, active.size, viewport);
      setSnapping(true);
      snapTimer.current = setTimeout(() => { setSnapping(false); snapTimer.current = null; }, 240);
      mascot.updateConfig(snapped);
    } else if (!active.longPressed) {
      mascot.interact();
    }
    setDragPosition(null); setDragSize(null);
  };

  const menuHeight = Math.min(560, Math.max(60, viewport.height - 24));
  const menuPoint = clampMascot({ x: actualSide === 'right' ? position.x + size.width - 224 : position.x, y: position.y - menuHeight - 8 }, { width: 224, height: menuHeight }, viewport, 12);

  if (!visible) return null;
  return createPortal(<div className="mascot-portal" data-mascot-root="true" onKeyDown={event => event.stopPropagation()} onKeyUp={event => event.stopPropagation()}>
    <div className={`mascot-widget${snapping ? ' is-snapping' : ''}${dragging || pressed ? ' is-dragging' : ''}`} data-testid="mascot-widget" data-state={state} data-reaction-id={reactionId} data-side={actualSide} data-anchor={config.anchor} data-mode="full" data-dragging={dragging} style={{ width: size.width, height: size.height, transform: `translate3d(${position.x}px, ${position.y}px, 0)` } as CSSProperties}>
      <button ref={characterButton} type="button" className="mascot-character-button" data-testid="mascot-drag-handle" aria-label="和 Lexi 打招呼，拖动调整位置" title="Lexi · 拖动调整位置，右键打开设置" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={cancelDrag} onLostPointerCapture={() => { if (drag.current) cancelDrag(); }} onContextMenu={event => { event.preventDefault(); cancelDrag(); setMenuOpen(true); }} onClick={event => { if (event.detail === 0) mascot.interact(); }} onDragStart={event => event.preventDefault()}>
        <MascotCharacter state={state} reactionId={reactionId} gesture={gesture} interactionEnabled={config.interactionEnabled} side={actualSide} pressed={pressed} dragging={dragging} />
      </button>
      <button type="button" className="mascot-menu-toggle" aria-label="Lexi 助手设置" aria-expanded={menuOpen} data-testid="mascot-menu-toggle" onPointerDown={event => event.stopPropagation()} onClick={() => setMenuOpen(value => !value)}><MoreHorizontal size={14} aria-hidden="true" /></button>
    </div>
    {bubble && <MascotBubbleMeasure text={bubble.text} widths={bubbleWidths} maxHeight={bubbleMaxHeight} onMeasure={measureBubble} />}
    {bubble && !menuOpen && !pressed && !dragging && (bubbleLayout?.placement
      ? <MascotBubble {...bubble} {...bubbleLayout.placement.bubble} width={bubbleLayout.bubbleSize.width} maxHeight={bubbleMaxHeight} side={bubbleLayout.placement.side} tailOffset={bubbleLayout.placement.tailOffset} dismiss={mascot.dismissBubble} />
      : <span className="mascot-accessible-status" role="status" aria-live="polite">{bubble.text}</span>)}
    {menuOpen && <MascotMenu x={menuPoint.x} y={menuPoint.y} maxHeight={menuHeight} scale={config.scale} idleBubbleEnabled={config.idleBubbleEnabled} interactionEnabled={config.interactionEnabled} interactionBlocked={interactionBlocked} onScale={scale => mascot.updateConfig({ scale })} onIdle={idleBubbleEnabled => mascot.updateConfig({ idleBubbleEnabled })} onInteraction={interactionEnabled => mascot.updateConfig({ interactionEnabled })} onGesture={kind => { mascot.playGesture(kind); closeMenu(); }} onReset={mascot.resetPosition} onHide={() => { closeMenu(); mascot.hide(); }} onClose={closeMenu} />}
  </div>, document.body);
}

export default MascotWidget;
