import { useEffect } from 'react';
import type { CSSProperties } from 'react';
import '../studioInteractions.css';

const openingPunctuation = /^[（(【\[「『《〈“‘]$/u;
const closingPunctuation = /^[，。！？、；：,.!?;:）)】\]」』》〉”’…%]$/u;

/** Keep Western words and CJK punctuation together, without making a whole title nowrap. */
function titleUnits(text: string): string[] {
  const graphemes = typeof Intl.Segmenter === 'function'
    ? Array.from(new Intl.Segmenter('zh', { granularity: 'grapheme' }).segment(text), part => part.segment)
    : Array.from(text);
  const units: string[] = [];
  let opening = '';
  for (const character of graphemes) {
    if (openingPunctuation.test(character)) {
      opening += character;
    } else if (closingPunctuation.test(character) && units.length && !/^\s+$/u.test(units[units.length - 1])) {
      units[units.length - 1] += character;
    } else if (/^[\p{Script=Latin}\p{Number}'’-]$/u.test(character)
      && units.length && /^[\p{Script=Latin}\p{Number}'’-]+$/u.test(units[units.length - 1]) && !opening) {
      units[units.length - 1] += character;
    } else {
      units.push(opening + character);
      opening = '';
    }
  }
  if (opening) units.push(opening);
  return units;
}

/** The accessible title is a single string; the visual characters remain decorative. */
export function MotionTitle({ text }: { text: string }) {
  let index = 0;
  return <span className="motion-title">
    <span className="motion-title__accessible">{text}</span>
    <span className="motion-title__visual" aria-hidden="true">
      {titleUnits(text).map((unit, unitIndex) => /^\s+$/u.test(unit)
        ? <span key={unitIndex}>{unit}</span>
        : <span className="motion-title__unit" key={unitIndex}>
          {Array.from(unit).map((character, characterIndex) => <span className="motion-title__character"
            key={characterIndex} style={{ '--motion-character-delay': `${Math.min(index++, 18) * 27}ms` } as CSSProperties}>
            <span>{character}</span>
          </span>)}
        </span>)}
    </span>
  </span>;
}

type Burst = { element: HTMLDivElement; timeout: number };

/** Opt-in decoration only. There is no application state, render loop, or synthetic activation. */
export default function StudioMotion() {
  useEffect(() => {
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let tilt: HTMLElement | null = null;
    let magnet: HTMLElement | null = null;
    let pendingTilt: HTMLElement | null = null;
    let pendingMagnet: HTMLElement | null = null;
    let frame = 0;
    let clientX = 0;
    let clientY = 0;
    let burstLayer: HTMLDivElement | null = null;
    const bursts: Burst[] = [];

    const clearElement = (element: HTMLElement | null) => {
      if (!element) return;
      delete element.dataset.motionActive;
      for (const property of ['--motion-rotate-x', '--motion-rotate-y', '--motion-x', '--motion-y']) {
        element.style.removeProperty(property);
      }
    };
    const resetPointer = () => {
      if (frame) window.cancelAnimationFrame(frame);
      frame = 0;
      clearElement(tilt);
      clearElement(magnet);
      tilt = null;
      magnet = null;
      pendingTilt = null;
      pendingMagnet = null;
    };
    const removeBurst = (burst: Burst) => {
      window.clearTimeout(burst.timeout);
      burst.element.remove();
      const index = bursts.indexOf(burst);
      if (index !== -1) bursts.splice(index, 1);
      if (!bursts.length) {
        burstLayer?.remove();
        burstLayer = null;
      }
    };
    const reset = () => {
      resetPointer();
      for (const burst of [...bursts]) removeBurst(burst);
    };
    const pointerAllowed = () => finePointer.matches && !reducedMotion.matches && !document.hidden;
    const isAvailable = (element: HTMLElement) => {
      if (!element.isConnected || element.closest('[inert], [hidden], [aria-disabled="true"], :disabled')
        || !element.getClientRects().length || getComputedStyle(element).visibility === 'hidden') return false;
      // Also honor modal focus boundaries in older dialogs which do not yet set inert.
      const dialogs = document.querySelectorAll<HTMLElement>('dialog[open], [role="dialog"][aria-modal="true"], [role="alertdialog"][aria-modal="true"]');
      const topDialog = Array.from(dialogs).reverse().find(dialog => !dialog.closest('[hidden], [inert]')
        && dialog.getClientRects().length && getComputedStyle(dialog).visibility !== 'hidden');
      return !topDialog || topDialog.contains(element);
    };
    const surfacesAt = (target: EventTarget | null) => {
      if (!(target instanceof Element)) return { nextTilt: null, nextMagnet: null };
      if (target.closest(':disabled, [aria-disabled="true"]')) return { nextTilt: null, nextMagnet: null };
      const nextTilt = target.closest<HTMLElement>('[data-motion-tilt]');
      const button = target.closest('button');
      const nextMagnet = button?.querySelector<HTMLElement>('[data-motion-magnet]') ?? null;
      return { nextTilt, nextMagnet };
    };
    const paint = () => {
      frame = 0;
      if (!pointerAllowed()) {
        resetPointer();
        return;
      }
      const nextTilt = pendingTilt && isAvailable(pendingTilt) ? pendingTilt : null;
      const nextMagnet = pendingMagnet && isAvailable(pendingMagnet) ? pendingMagnet : null;
      if (tilt !== nextTilt) clearElement(tilt);
      if (magnet !== nextMagnet) clearElement(magnet);
      tilt = nextTilt;
      magnet = nextMagnet;
      if (tilt) {
        const bounds = tilt.getBoundingClientRect();
        const x = Math.max(-1, Math.min(1, ((clientX - bounds.left) / bounds.width - .5) * 2));
        const y = Math.max(-1, Math.min(1, ((clientY - bounds.top) / bounds.height - .5) * 2));
        tilt.style.setProperty('--motion-rotate-x', `${(-y * 5).toFixed(2)}deg`);
        tilt.style.setProperty('--motion-rotate-y', `${(x * 5).toFixed(2)}deg`);
        tilt.dataset.motionActive = 'true';
      }
      if (magnet) {
        const button = magnet.closest('button');
        const bounds = (button ?? magnet).getBoundingClientRect();
        const x = Math.max(-1, Math.min(1, ((clientX - bounds.left) / bounds.width - .5) * 2));
        const y = Math.max(-1, Math.min(1, ((clientY - bounds.top) / bounds.height - .5) * 2));
        magnet.style.setProperty('--motion-x', `${(x * 5).toFixed(2)}px`);
        magnet.style.setProperty('--motion-y', `${(y * 5).toFixed(2)}px`);
        magnet.dataset.motionActive = 'true';
      }
    };
    const onPointerMove = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || !pointerAllowed()) {
        resetPointer();
        return;
      }
      const { nextTilt, nextMagnet } = surfacesAt(event.target);
      if (!nextTilt && !nextMagnet) {
        resetPointer();
        return;
      }
      pendingTilt = nextTilt;
      pendingMagnet = nextMagnet;
      clientX = event.clientX;
      clientY = event.clientY;
      if (!frame) frame = window.requestAnimationFrame(paint);
    };
    const onPointerOut = (event: PointerEvent) => {
      if (!tilt && !magnet && !pendingTilt && !pendingMagnet) return;
      const { nextTilt, nextMagnet } = surfacesAt(event.relatedTarget);
      if (nextTilt !== pendingTilt || nextMagnet !== pendingMagnet) resetPointer();
    };
    const onClick = (event: MouseEvent) => {
      if (document.hidden || reducedMotion.matches || !(event.target instanceof Element)) return;
      const button = event.target.closest<HTMLButtonElement>('button[data-motion-burst]');
      if (!button || !isAvailable(button)) return;
      if (bursts.length >= 3) removeBurst(bursts[0]);
      const bounds = button.getBoundingClientRect();
      const useClickPoint = event.detail > 0 && event.clientX >= bounds.left && event.clientX <= bounds.right
        && event.clientY >= bounds.top && event.clientY <= bounds.bottom;
      const x = useClickPoint ? event.clientX : bounds.left + bounds.width / 2;
      const y = useClickPoint ? event.clientY : bounds.top + bounds.height / 2;
      if (!burstLayer) {
        burstLayer = document.createElement('div');
        burstLayer.className = 'motion-burst-layer';
        burstLayer.setAttribute('aria-hidden', 'true');
        document.body.append(burstLayer);
      }
      const element = document.createElement('div');
      element.className = 'motion-click-burst';
      element.style.setProperty('--burst-x', `${x}px`);
      element.style.setProperty('--burst-y', `${y}px`);
      const ring = document.createElement('span');
      ring.className = 'motion-click-burst__ring';
      element.append(ring);
      for (let index = 0; index < 8; index++) {
        const ray = document.createElement('span');
        ray.className = 'motion-click-burst__ray';
        ray.style.setProperty('--burst-angle', `${index * 45}deg`);
        ray.style.setProperty('--burst-length', `${index % 2 ? 7 : 11}px`);
        element.append(ray);
      }
      burstLayer.append(element);
      const burst: Burst = { element, timeout: 0 };
      burst.timeout = window.setTimeout(() => removeBurst(burst), 820);
      bursts.push(burst);
      element.addEventListener('animationend', event => {
        if (event.target === element) removeBurst(burst);
      }, { once: false });
    };
    const onPreferenceChange = () => { if (!pointerAllowed()) reset(); };
    const onKeyDown = (event: KeyboardEvent) => {
      resetPointer();
      if (event.key === 'Escape') reset();
    };

    document.addEventListener('pointermove', onPointerMove, { passive: true });
    document.addEventListener('pointerout', onPointerOut, { passive: true });
    document.addEventListener('pointerdown', resetPointer, { passive: true });
    // Observe a real activation before its React handler can navigate or unmount the button.
    document.addEventListener('click', onClick, { capture: true, passive: true });
    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('visibilitychange', onPreferenceChange);
    window.addEventListener('scroll', reset, { capture: true, passive: true });
    window.addEventListener('blur', reset);
    window.addEventListener('resize', reset, { passive: true });
    finePointer.addEventListener('change', onPreferenceChange);
    reducedMotion.addEventListener('change', onPreferenceChange);
    return () => {
      reset();
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerout', onPointerOut);
      document.removeEventListener('pointerdown', resetPointer);
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('visibilitychange', onPreferenceChange);
      window.removeEventListener('scroll', reset, true);
      window.removeEventListener('blur', reset);
      window.removeEventListener('resize', reset);
      finePointer.removeEventListener('change', onPreferenceChange);
      reducedMotion.removeEventListener('change', onPreferenceChange);
    };
  }, []);
  return null;
}
