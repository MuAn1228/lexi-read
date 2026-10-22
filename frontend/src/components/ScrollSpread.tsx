import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

/** Scroll-expand treatment for the import cover only, never the reading canvas. */
export default function ScrollSpread({ children }: { children: ReactNode }) {
  const cover = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = cover.current;
    if (!element) return;
    const preference = matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    const update = () => {
      frame = 0;
      const absoluteTop = element.getBoundingClientRect().top + window.scrollY;
      const progress = preference.matches ? 1 : Math.max(0, Math.min(1, window.scrollY / Math.max(180, absoluteTop)));
      element.style.setProperty('--spread-inset', `${(1 - progress) * 5}%`);
      element.style.setProperty('--spread-radius', `${32 - progress * 18}px`);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
    const visibility = () => { if (document.hidden) { cancelAnimationFrame(frame); frame = 0; } else schedule(); };
    update();
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    preference.addEventListener('change', schedule);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      preference.removeEventListener('change', schedule);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, []);
  return <div className="scroll-spread" ref={cover}><div className="scroll-spread__sheet">{children}</div></div>;
}
