import { Bot, Eye, EyeOff } from 'lucide-react';
import { useMascot, useMascotSnapshot } from './useMascot';

/** A permanent restore entry, independent of the floating character's visibility. */
export function MascotControls() {
  const mascot = useMascot();
  const { config } = useMascotSnapshot();
  return <span className="mascot-header-controls"><button type="button" className="mascot-header-toggle text-button" aria-label="向 Lexi AI 学习助手提问" onClick={() => mascot.openAssistant()}><Bot size={16} aria-hidden="true" /><span>问 Lexi</span></button><button type="button" className="mascot-header-visibility text-button"
    aria-label={config.hidden ? '显示阅读助手 Lexi' : '隐藏阅读助手 Lexi'}
    aria-pressed={!config.hidden}
    onClick={() => config.hidden ? mascot.show() : mascot.hide()}>
    {config.hidden ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
  </button></span>;
}
