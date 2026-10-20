import { useEffect } from 'react';

/** Decorative, delegated pointer light. It never changes application or learning state. */
export default function StudioEffects() {
  useEffect(() => {
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let current: HTMLElement | null = null;
    let pending: HTMLElement | null = null;
    let frame = 0;
    let pointerX = 0;
    let pointerY = 0;

    const clearSurface = (surface: HTMLElement | null) => {
      if (!surface) return;
      delete surface.dataset.studioActive;
      surface.style.removeProperty('--spot-x');
      surface.style.removeProperty('--spot-y');
    };
    const reset = () => {
      if (frame) window.cancelAnimationFrame(frame);
      frame = 0;
      clearSurface(current);
      current = null;
      pending = null;
    };
    const isAllowed = () => finePointer.matches && !reducedMotion.matches && !document.hidden;
    const isAvailable = (surface: HTMLElement) => surface.isConnected
      && !surface.closest('[inert], [hidden], [aria-disabled="true"], :disabled')
      && !surface.querySelector(':scope > [inert]');

    const paint = () => {
      frame = 0;
      if (!isAllowed() || !pending || !isAvailable(pending)) {
        reset();
        return;
      }
      if (current !== pending) {
        clearSurface(current);
        current = pending;
      }
      const bounds = current.getBoundingClientRect();
      if (!bounds.width || !bounds.height) {
        reset();
        return;
      }
      const x = Math.max(0, Math.min(100, ((pointerX - bounds.left) / bounds.width) * 100));
      const y = Math.max(0, Math.min(100, ((pointerY - bounds.top) / bounds.height) * 100));
      current.style.setProperty('--spot-x', `${x.toFixed(1)}%`);
      current.style.setProperty('--spot-y', `${y.toFixed(1)}%`);
      current.dataset.studioActive = 'true';
    };
    const onPointerMove = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || !isAllowed() || !(event.target instanceof Element)) {
        reset();
        return;
      }
      const surface = event.target.closest<HTMLElement>('.studio-spotlight');
      // Disabled controls retain their disabled appearance, including inside a lit card.
      if (!surface || !isAvailable(surface) || event.target.closest(':disabled, [aria-disabled="true"]')) {
        reset();
        return;
      }
      pending = surface;
      pointerX = event.clientX;
      pointerY = event.clientY;
      if (!frame) frame = window.requestAnimationFrame(paint);
    };
    const onPointerOut = (event: PointerEvent) => {
      if (!current && !pending) return;
      const next = event.relatedTarget;
      const nextSurface = next instanceof Element ? next.closest('.studio-spotlight') : null;
      if (nextSurface !== (pending || current)) reset();
    };
    const onPreferenceChange = () => { if (!isAllowed()) reset(); };

    document.addEventListener('pointermove', onPointerMove, { passive: true });
    document.addEventListener('pointerout', onPointerOut, { passive: true });
    document.addEventListener('pointerdown', reset, { passive: true });
    document.addEventListener('keydown', reset);
    document.addEventListener('visibilitychange', onPreferenceChange);
    window.addEventListener('blur', reset);
    window.addEventListener('scroll', reset, { capture: true, passive: true });
    finePointer.addEventListener('change', onPreferenceChange);
    reducedMotion.addEventListener('change', onPreferenceChange);
    return () => {
      reset();
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerout', onPointerOut);
      document.removeEventListener('pointerdown', reset);
      document.removeEventListener('keydown', reset);
      document.removeEventListener('visibilitychange', onPreferenceChange);
      window.removeEventListener('blur', reset);
      window.removeEventListener('scroll', reset, true);
      finePointer.removeEventListener('change', onPreferenceChange);
      reducedMotion.removeEventListener('change', onPreferenceChange);
    };
  }, []);

  return null;
}
