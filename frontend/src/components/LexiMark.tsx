/** A small open-frame seal: the space around a word is part of the mark. */
export default function LexiMark({ className = '' }: { className?: string }) {
  return <svg className={className} width="32" height="32" viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <path d="M3 10V3H29V29H22M3 15V29H17" stroke="currentColor" strokeWidth="2" />
    <path d="M11 10H22V22H11V10ZM11 16H22" stroke="currentColor" strokeWidth="1.6" />
    <path d="M6 3V7" stroke="currentColor" strokeWidth="2" />
  </svg>;
}
