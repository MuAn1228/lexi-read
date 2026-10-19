import { useEffect, useId, useRef, useState } from 'react';
import type { PointerEvent } from 'react';
import './ReadingGarden.css';

type ReadingGardenProps = {
  compact?: boolean;
};

const greetings = [
  '你好呀，一起翻开新的好奇心。',
  '小芽向你挥了挥叶子。',
  '下一页，会遇见什么呢？',
];

/** A little reading companion. Its reactions are purely visual and never record learning. */
export default function ReadingGarden({ compact = false }: ReadingGardenProps) {
  const root = useRef<HTMLDivElement>(null);
  const canTilt = useRef(false);
  const greetingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [greetingCount, setGreetingCount] = useState(0);
  const [greetingActive, setGreetingActive] = useState(false);
  const drawingId = useId().replace(/:/g, '');

  const resetTilt = () => {
    root.current?.style.setProperty('--garden-x', '0deg');
    root.current?.style.setProperty('--garden-y', '0deg');
  };

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
    const updateMotion = () => {
      canTilt.current = !reducedMotion.matches && finePointer.matches;
      resetTilt();
    };
    updateMotion();
    reducedMotion.addEventListener('change', updateMotion);
    finePointer.addEventListener('change', updateMotion);
    return () => {
      reducedMotion.removeEventListener('change', updateMotion);
      finePointer.removeEventListener('change', updateMotion);
      if (greetingTimer.current !== null) clearTimeout(greetingTimer.current);
    };
  }, []);

  const followPointer = (event: PointerEvent<HTMLDivElement>) => {
    if (!canTilt.current || event.pointerType !== 'mouse') return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const horizontal = Math.max(-1, Math.min(1, (event.clientX - bounds.left) / bounds.width * 2 - 1));
    const vertical = Math.max(-1, Math.min(1, (event.clientY - bounds.top) / bounds.height * 2 - 1));
    event.currentTarget.style.setProperty('--garden-x', `${(horizontal * 7).toFixed(2)}deg`);
    event.currentTarget.style.setProperty('--garden-y', `${(-vertical * 5).toFixed(2)}deg`);
  };

  const greet = () => {
    if (greetingTimer.current !== null) clearTimeout(greetingTimer.current);
    setGreetingCount((count) => count + 1);
    setGreetingActive(true);
    greetingTimer.current = setTimeout(() => {
      setGreetingActive(false);
      greetingTimer.current = null;
    }, 1400);
  };

  return <div
    ref={root}
    className={`reading-garden${compact ? ' reading-garden--compact' : ''}`}
    data-greeting={greetingActive ? 'active' : 'rest'}
    onPointerMove={followPointer}
    onPointerLeave={resetTilt}
  >
    <div className="reading-garden__scene" aria-hidden="true">
      <div className="reading-garden__tilt">
      <svg className="reading-garden__drawing" viewBox={compact ? '96 30 272 206' : '0 0 440 246'} fill="none">
        <defs>
          <linearGradient id={`${drawingId}-paper`} x1="167" y1="129" x2="225" y2="216" gradientUnits="userSpaceOnUse">
            <stop stopColor="#fffef6"/><stop offset="1" stopColor="#f7eac7"/>
          </linearGradient>
          <linearGradient id={`${drawingId}-leaf`} x1="218" y1="55" x2="251" y2="117" gradientUnits="userSpaceOnUse">
            <stop stopColor="#e3f494"/><stop offset="1" stopColor="#a4cd4e"/>
          </linearGradient>
        </defs>
        <g className="reading-garden__backdrop">
          <ellipse cx="231" cy="204" rx="161" ry="28" fill="#092f2a" fillOpacity=".16"/>
          <ellipse cx="224" cy="134" rx="151" ry="94" fill="#e3f3bd" fillOpacity=".07"/>
          <circle cx="340" cy="60" r="25" fill="#ffc5a3"/>
          <circle cx="347" cy="54" r="6" fill="#ffe0c1"/>
          <path d="M54 123h12m-6-6v12M354 126h10m-5-5v10" stroke="#b8d7bb" strokeWidth="2" strokeLinecap="round"/>
          <circle cx="318" cy="187" r="3" fill="#d8ec9c"/>
          <circle cx="94" cy="75" r="2.5" fill="#ebd09b"/>
          <path d="M366 172c-4-10 2-18 11-21 3 9-1 18-11 21Z" fill="#92b997"/>
          <path d="M368 171c5-4 11-2 15 3-5 5-11 4-15-3Z" fill="#c6d89a"/>
          <path d="M75 178c-3-7-1-14 5-17 6 7 5 13-5 17Z" fill="#aacb89"/>
        </g>
        <g className="reading-garden__world">
          <g className="reading-garden__word-card">
            <path d="m64 93 65-15 12 43-65 15Z" fill="#ffccb4"/>
            <path d="m68 91 64-14 11 42-64 14Z" fill="#ffe7d2"/>
            <path d="m80 107 37-8m-34 17 25-6" stroke="#ad795d" strokeWidth="3" strokeLinecap="round"/>
            <circle cx="128" cy="85" r="4" fill="#e9a47e"/>
          </g>
          <g className="reading-garden__bookmark">
            <path d="m313 98 25 10-15 37-9-12-15 2Z" fill="#edb66e"/>
            <path d="m316 104 12 5" stroke="#fff0c7" strokeWidth="2" strokeLinecap="round"/>
          </g>
          <g className="reading-garden__book">
            <path d="M112 150c37-18 73-13 110 12 38-25 76-29 112-9l-2 53c-37-15-73-9-110 13-34-22-73-28-111-12l1-57Z" fill="#0f443c"/>
            <path d="M114 148c34-14 71-8 108 15 36-23 75-29 110-13l-4 50c-39-11-72-6-106 14-35-20-69-26-107-13l-1-53Z" fill="#afd0ad"/>
            <path d="M119 146c34-17 70-10 103 12 33-22 71-27 104-11l-4 48c-37-11-69-5-100 14-32-19-65-25-101-12l-2-51Z" fill="#f6e7c6"/>
            <path d="M122 137c35-13 67-7 100 16v51c-32-19-63-26-98-15l-2-52Z" fill={`url(#${drawingId}-paper)`}/>
            <path d="M222 153c33-23 65-29 100-16l-4 52c-34-10-65-4-96 15v-51Z" fill="#fffcf0"/>
            <path d="M222 155v48" stroke="#d1be93" strokeWidth="2" strokeLinecap="round"/>
            <path d="M137 151c20-3 40 0 59 10m-58 2c17-2 33 2 48 9m-46 3c20 0 36 5 51 13" stroke="#c4c6a7" strokeWidth="3.5" strokeLinecap="round"/>
            <path d="M247 158c19-7 36-10 56-8m-53 19c16-5 32-7 49-5m-48 17c13-4 26-6 39-5" stroke="#c4c6a7" strokeWidth="3.5" strokeLinecap="round"/>
            <path d="m258 155 28-4 1 10-28 3Z" fill="#f6c38e" fillOpacity=".8"/>
            <path d="m198 200 8 4-3 25-8-8-9 4Z" fill="#f7a680"/>
          </g>
          <g key={`sprout-${greetingCount}`} className={`reading-garden__sprout${greetingCount ? ' reading-garden__sprout--greet' : ''}`}>
            <path d="M223 149c-1-20-2-38 2-55" stroke="#9dc878" strokeWidth="7" strokeLinecap="round"/>
            <path d="M223 124c-32 5-48-13-45-34 26-4 45 9 45 34Z" fill="#8dbb70"/>
            <path d="M224 112c-5-31 13-54 41-54 5 29-11 51-41 54Z" fill={`url(#${drawingId}-leaf)`}/>
            <path d="M224 109c9-15 20-27 32-37M217 119c-10-8-18-14-28-19" stroke="#729f59" strokeWidth="1.8" strokeLinecap="round"/>
            <ellipse cx="223" cy="127" rx="22" ry="20" fill="#d9ec9d"/>
            <path d="M216 123v3m14-3v3" stroke="#2c5840" strokeWidth="2.8" strokeLinecap="round"/>
            <path d="M217 132c3 4 8 4 11 0" stroke="#2c5840" strokeWidth="2" strokeLinecap="round"/>
            <ellipse cx="210" cy="130" rx="3.5" ry="2" fill="#f3b58a"/>
            <ellipse cx="236" cy="130" rx="3.5" ry="2" fill="#f3b58a"/>
          </g>
          <g className="reading-garden__hello-note">
            <rect x="273" y="35" width="94" height="29" rx="14.5" fill="#eff5d9"/>
            <path d="m287 60 2 11 11-9" fill="#eff5d9"/>
            <text x="320" y="54" textAnchor="middle" fill="#38634c" fontSize="12" fontFamily="Georgia, serif" fontStyle="italic">hello, curiosity.</text>
          </g>
        </g>
        {greetingCount > 0 && <g key={`sparkles-${greetingCount}`} className="reading-garden__sparkles">
          <path className="reading-garden__spark reading-garden__spark--one" d="m171 57 3 9 9 3-9 3-3 9-3-9-9-3 9-3Z" fill="#e0ef92"/>
          <path className="reading-garden__spark reading-garden__spark--two" d="m279 88 2 6 6 2-6 2-2 6-2-6-6-2 6-2Z" fill="#ffcca3"/>
          <circle className="reading-garden__spark reading-garden__spark--three" cx="251" cy="35" r="3.5" fill="#eff4bf"/>
          <circle className="reading-garden__spark reading-garden__spark--four" cx="147" cy="116" r="3" fill="#ffc09c"/>
        </g>}
      </svg>
      </div>
    </div>
    <button type="button" className="garden-greeting" onClick={greet}>
      <svg width="14" height="14" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <path d="M10 17v-6m0 0C4 12 2 8 3 4c5 0 8 2 7 7Zm0 0c-1-6 2-9 7-9 1 5-1 9-7 9Z" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
      </svg>
      <span>{compact ? '和小芽打招呼' : '和小芽打个招呼'}</span>
    </button>
    <p className="reading-garden__message" aria-hidden="true">
      {greetingCount ? greetings[(greetingCount - 1) % greetings.length] : '一点好奇心，长出新的故事。'}
    </p>
    <span className="reading-garden__announcement" role="status" aria-live="polite" aria-atomic="true">
      {greetingCount ? greetings[(greetingCount - 1) % greetings.length] : ''}
    </span>
  </div>;
}
