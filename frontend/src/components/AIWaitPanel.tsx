import { useEffect, useState } from 'react';
import { Square } from 'lucide-react';
import type { AIStreamPhase } from '../aiStream';
import { aiWaitElapsedSeconds, aiWaitIsSlow, aiWaitPhaseLabel } from '../aiWaitState';
import './AIWaitPanel.css';

export default function AIWaitPanel({ label, phase, onStop, preview, compact = false }: {
  label: string; phase?: AIStreamPhase; onStop?: () => void; preview?: string; compact?: boolean;
}) {
  const [startedAt] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setElapsed(aiWaitElapsedSeconds(startedAt, Date.now())), 1_000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  return <div className={`ai-wait-panel${compact ? ' ai-wait-panel-compact' : ''}`}>
    <div className="ai-wait-heading">
      <div className="ai-wait-status" role="status" aria-live="polite" aria-atomic="true">
        <strong>{label}</strong><span><i className="ai-wait-dot" aria-hidden="true" />{aiWaitPhaseLabel(phase)}</span>
      </div>
      <span className="ai-wait-elapsed" aria-hidden="true">已用 {elapsed} 秒</span>
      {onStop && <button type="button" className="text-button ai-wait-stop" onClick={onStop}><Square size={13} aria-hidden="true" />停止等待</button>}
    </div>
    {aiWaitIsSlow(elapsed) && <p className="ai-wait-slow" role="status">已等待较久，可以继续等待，也可以停止后重试。</p>}
    {preview && <div className="ai-wait-preview" aria-live="off"><p className="ai-wait-draft-label">生成中草稿，完成后核对</p><div className="ai-wait-draft">{preview}</div></div>}
  </div>;
}
