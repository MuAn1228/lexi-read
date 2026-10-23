import { useEffect, useId } from 'react';
import { LoaderCircle, Square, Volume2 } from 'lucide-react';
import { useSpeech } from './SpeechProvider';
import { speechTextProblem } from './speech';
import type { SpeechAccent, SpeechRate } from './speech';
import './pronunciation.css';

export default function PronunciationButton({ text, label = '朗读', speechKey, compact = false, className = '' }: {
  text: string; label?: string; speechKey?: string; compact?: boolean; className?: string;
}) {
  const speech = useSpeech();
  const id = useId();
  const key = speechKey ?? `speech:${id}`;
  const active = speech.activeKey === key;
  const problem = speechTextProblem(text);
  const title = !speech.isSupported ? '当前浏览器不支持发音，请使用新版 Edge 或 Chrome。'
    : !speech.voiceAvailable ? '设备未提供所选英语口音，请在发音设置中切换口音或安装英语语音。'
      : problem || (active ? `停止${label}` : label);
  const { stop } = speech;
  useEffect(() => () => stop(key), [stop, key, text]);
  return <button type="button" className={`pronunciation-button${active ? ' is-speaking' : ''}${compact ? ' pronunciation-button--compact' : ''} ${className}`}
    disabled={!speech.isSupported || !speech.voiceAvailable || Boolean(problem)} aria-pressed={active}
    aria-label={active ? `停止${label}` : label} title={title}
    onClick={() => active ? speech.stop(key) : speech.speak(text, { key })}>
    {active ? speech.status === 'starting' ? <LoaderCircle size={15} className="spin" /> : <Square size={13} fill="currentColor" /> : <Volume2 size={15} />}
    {!compact && <span>{active ? speech.status === 'starting' ? '准备发音…' : '停止' : label}</span>}
  </button>;
}

export function SpeechSettings() {
  const speech = useSpeech();
  const accentId = useId(); const rateId = useId();
  return <div className="speech-settings">
    <div className="speech-settings-controls">
      <label htmlFor={accentId}>发音<select id={accentId} aria-label="发音口音" disabled={!speech.isSupported} value={speech.accent} onChange={event => speech.setAccent(event.target.value as SpeechAccent)}><option value="en-US">美式英语</option><option value="en-GB">英式英语</option></select></label>
      <label htmlFor={rateId}>语速<select id={rateId} aria-label="朗读语速" disabled={!speech.isSupported} value={speech.rate} onChange={event => speech.setRate(Number(event.target.value) as SpeechRate)}><option value={0.8}>慢速 · 0.8×</option><option value={1}>正常 · 1×</option><option value={1.2}>快速 · 1.2×</option></select></label>
      {speech.rate !== 1 && <button type="button" className="speech-rate-reset" onClick={() => speech.setRate(1)}>恢复正常语速</button>}
    </div>
    {speech.voiceName && <p className="speech-source">{speech.engine === 'neural' ? '本机自然发音' : '系统语音'}<span>·</span>{speech.voiceName}</p>}
    <SpeechStatus />
  </div>;
}

export function SpeechStatus() {
  const speech = useSpeech();
  const unavailable = !speech.isSupported ? '当前浏览器不支持发音，请使用新版 Edge 或 Chrome。'
    : !speech.voiceAvailable ? `设备尚未提供${speech.accent === 'en-US' ? '美式' : '英式'}英语语音。可切换口音，或在系统设置中安装对应英语语音。` : '';
  return speech.error || unavailable || speech.notice ? <p className="speech-status" role="status">{speech.error || unavailable || speech.notice}</p> : null;
}
