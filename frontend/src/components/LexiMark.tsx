import { Leaf } from 'lucide-react';

/** The original leaf mark, shared by the header and footer. */
export default function LexiMark({ className = '' }: { className?: string }) {
  return <Leaf className={className} size={21} aria-hidden="true" />;
}
