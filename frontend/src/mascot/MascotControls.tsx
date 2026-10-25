import { Bot } from 'lucide-react';
import { useMascot, useMascotSnapshot } from './useMascot';

/** A permanent restore entry, independent of the floating character's visibility. */
export function MascotControls() {
  const mascot = useMascot();
  const { config } = useMascotSnapshot();
  return <button type="button" className="mascot-header-toggle text-button"
    aria-label={config.hidden ? '显示阅读助手 Lexi' : '隐藏阅读助手 Lexi'}
    aria-pressed={!config.hidden}
    onClick={() => config.hidden ? mascot.show() : mascot.hide()}>
    <Bot size={16} aria-hidden="true" /><span>阅读助手</span>
  </button>;
}
