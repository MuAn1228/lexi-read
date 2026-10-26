import { Check, Minus, X } from 'lucide-react';
import type { WordStatus } from './types';

export const statusLabels: Record<WordStatus, string> = { KNOWN: '已掌握', UNKNOWN: '不认识', UNCERTAIN: '不确定' };
const statusIcons = { KNOWN: Check, UNKNOWN: X, UNCERTAIN: Minus };
const statuses: WordStatus[] = ['KNOWN', 'UNKNOWN', 'UNCERTAIN'];

export function StatusBadge({ status }: { status: WordStatus | null }) {
  return <span className={`status-badge ${status?.toLowerCase() ?? 'unmarked'}`}><span className="status-dot" />{status ? statusLabels[status] : '未标记'}</span>;
}

export function WordControls({ word, status, onMark, disabled, compact = false, showLabels = false, disableCurrent = false }: {
  word: string; status: WordStatus | null; onMark: (status: WordStatus) => void; disabled: boolean; compact?: boolean; showLabels?: boolean; disableCurrent?: boolean;
}) {
  return <div className={`word-controls ${compact ? 'compact' : ''} ${showLabels ? 'with-labels' : ''}`} aria-label={`${word} 的掌握程度`}>
    {statuses.map((value, index) => {
      const Icon = statusIcons[value];
      return <button key={value} className={`mark-button ${value.toLowerCase()} ${status === value ? 'is-marked' : ''}`}
        aria-label={`${word}：${statusLabels[value]}`} title={`${statusLabels[value]}${compact ? '' : `（快捷键 ${index + 1}）`}`}
        aria-pressed={status === value} disabled={disabled || (disableCurrent && status === value)}
        onClick={(event) => { event.stopPropagation(); onMark(value); }}>
        <Icon size={compact ? 13 : 15} strokeWidth={2.4} />{(showLabels || !compact) && <span>{statusLabels[value]}</span>}{!compact && <kbd>{index + 1}</kbd>}
      </button>;
    })}
  </div>;
}
