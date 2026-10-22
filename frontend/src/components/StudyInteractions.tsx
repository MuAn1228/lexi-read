import { Heart, Send } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import './StudyInteractions.css';

// These are small, original interpretations of the React Bits micro interactions.
// Only an icon moves; the button, reader, and focus target always keep their place.
function useMotionState() {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onVisibility = () => setVisible(document.visibilityState !== 'hidden');
    const onPreference = () => setReduced(media.matches);
    document.addEventListener('visibilitychange', onVisibility);
    media.addEventListener('change', onPreference);
    onVisibility(); onPreference();
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      media.removeEventListener('change', onPreference);
    };
  }, []);
  return { visible, animate: visible && !reduced };
}

const flightFrames: Keyframe[] = [
  { transform: 'translate(0, 0) rotate(0)', opacity: 1, offset: 0 },
  { transform: 'translate(-2px, 2px) rotate(-12deg)', opacity: 1, offset: .18 },
  { transform: 'translate(13px, -13px) rotate(7deg)', opacity: 0, offset: .52 },
  { transform: 'translate(-6px, 6px) rotate(-8deg)', opacity: 0, offset: .56 },
  { transform: 'translate(0, 0) rotate(0)', opacity: 1, offset: 1 },
];
const heartFrames: Keyframe[] = [
  { transform: 'scale(1)', offset: 0 },
  { transform: 'scale(.3)', offset: .3 },
  { transform: 'scale(1.35)', offset: .56 },
  { transform: 'scale(.9)', offset: .8 },
  { transform: 'scale(1)', offset: 1 },
];
const paperFrames: Keyframe[] = [
  { transform: 'scale(1) rotate(0) skew(0)', opacity: 1, offset: 0 },
  { transform: 'scale(.8, .9) rotate(-12deg) skew(12deg)', opacity: .8, offset: .2 },
  { transform: 'scale(.25, .35) rotate(38deg) skew(-18deg)', opacity: .45, offset: .53 },
  { transform: 'scale(.2) rotate(55deg)', opacity: 0, offset: .7 },
  { transform: 'scale(1) rotate(0) skew(0)', opacity: 0, offset: .74 },
  { transform: 'scale(1) rotate(0) skew(0)', opacity: 1, offset: 1 },
];

function useGlyphRun(run: number, active: boolean, frames: Keyframe[], duration: number) {
  const glyph = useRef<HTMLSpanElement>(null);
  const previous = useRef(run);
  const { animate } = useMotionState();
  useEffect(() => {
    if (run === previous.current) return;
    previous.current = run;
    if (!active || !animate || !glyph.current?.animate) return;
    const animation = glyph.current.animate(frames, { duration, easing: 'cubic-bezier(.2,.7,.25,1)' });
    return () => animation.cancel();
  }, [run, active, animate, frames, duration]);
  return glyph;
}

/** The counter changes only once a valid question is actually submitted. */
export function FlyingSendIcon({ run }: { run: number }) {
  const glyph = useGlyphRun(run, true, flightFrames, 620);
  return <span ref={glyph} className="study-glyph study-send-glyph" data-study-flight={run} aria-hidden="true"><Send size={15} /></span>;
}

/** The counter changes after the server confirms a review, never on a mere tap. */
export function PulseHeart({ run, active }: { run: number; active: boolean }) {
  const glyph = useGlyphRun(run, active, heartFrames, 560);
  return <span ref={glyph} className={`study-glyph study-review-heart ${run ? 'is-confirmed' : ''}`} data-study-pulse={run} aria-hidden="true"><Heart size={14} fill="currentColor" strokeWidth={1.5} /></span>;
}

/** An icon-sized scrap, not a transform on the conversation or an extra delete. */
export function PaperCrumpleIcon({ run }: { run: number }) {
  const glyph = useGlyphRun(run, true, paperFrames, 650);
  return <span ref={glyph} className="study-glyph study-paper-glyph" data-study-crumple={run} aria-hidden="true"><svg width="15" height="16" viewBox="0 0 20 22" fill="none"><path d="M4 2H12L17 7V20H4V2Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /><path d="M12 2V7H17M7 11H14M7 15H12" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg></span>;
}

/** An honest waiting line: no invented stages or model reasoning is displayed. */
export function ThoughtLine({ label, className = '' }: { label: string; className?: string }) {
  const [started] = useState(Date.now);
  const [elapsed, setElapsed] = useState(0);
  const { visible, animate } = useMotionState();
  useEffect(() => {
    if (!visible) return;
    const tick = () => setElapsed(Math.floor((Date.now() - started) / 1000));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [visible, started]);
  return <div className={`study-thought-line ${className}`} data-motion={animate ? 'on' : 'off'} role="status">
    <span className="study-thought-line__glyph" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 3L14.5 9.5L21 12L14.5 14.5L12 21L9.5 14.5L3 12L9.5 9.5L12 3Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg></span>
    <span className="study-thought-line__label">{label}</span>
    <span className="study-thought-line__elapsed" aria-hidden="true">{elapsed}s</span>
  </div>;
}
