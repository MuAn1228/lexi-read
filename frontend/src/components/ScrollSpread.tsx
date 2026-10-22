import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

/** Expand the import surface over the page's natural scroll, without adding space. */
export default function ScrollSpread({ children }: { children: ReactNode }) {
  const cover = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = cover.current;
    if (!element) return;
    const preference = matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let scrollRange = 0;
    const update = () => {
      frame = 0;
      const progress = preference.matches || !scrollRange ? 1 : Math.max(0, Math.min(1, window.scrollY / scrollRange));
      const inset = window.innerWidth <= 700 ? 6 : 9;
      element.style.setProperty('--spread-inset', `${(1 - progress) * inset}%`);
      element.style.setProperty('--spread-radius', `${30 - progress * 18}px`);
      element.style.setProperty('--spread-shadow', String((1 - progress) * .09));
    };
    const schedule = () => { if (!document.hidden && !frame) frame = requestAnimationFrame(update); };
    const measure = () => {
      const available = document.documentElement.scrollHeight - window.innerHeight;
      // Cache the range: width changes while scrolling must not move its endpoint.
      scrollRange = available < 24 ? 0 : Math.min(220, available);
      schedule();
    };
    const visibility = () => { if (document.hidden) { cancelAnimationFrame(frame); frame = 0; } else schedule(); };
    measure();
    const resize = new ResizeObserver(() => { if (window.scrollY === 0) measure(); });
    resize.observe(document.body);
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', measure);
    preference.addEventListener('change', schedule);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', measure);
      preference.removeEventListener('change', schedule);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, []);
  return <div className="scroll-spread" ref={cover}><div className="scroll-spread__sheet">{children}</div></div>;
}
