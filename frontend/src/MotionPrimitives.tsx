import { useEffect, useState } from 'react';
import { Check } from 'lucide-react';

/** Show the authoritative value immediately; only its ink changes on updates. */
export function MotionNumber({ value, formatted = false }: { value: number; formatted?: boolean }) {
  const text = formatted ? value.toLocaleString() : String(value);
  return <span className="motion-number"><span key={text} className="motion-number-ink">{text}</span></span>;
}

/** Keep a cleared toast just long enough to fade, without extending the live message. */
export function SavedToast({ message }: { message: string }) {
  const [lastMessage, setLastMessage] = useState(message);
  useEffect(() => {
    if (message) { setLastMessage(message); return; }
    const timer = window.setTimeout(() => setLastMessage(''), 180);
    return () => window.clearTimeout(timer);
  }, [message]);
  const visibleText = message || lastMessage;
  return <>
    <span className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">{message}</span>
    {visibleText && <div className={`toast motion-toast${message ? '' : ' is-leaving'}`} aria-hidden="true"><Check size={17} /><span key={visibleText}>{visibleText}</span></div>}
  </>;
}
