import { useEffect, useId, useRef } from 'react';
import { LoaderCircle, Square, Volume2 } from 'lucide-react';
import { useSpeech } from './SpeechProvider';
import { pronunciationSourceLabel, speechTextProblem } from './speech';
import type { SpeechAccent, SpeechRate } from './speech';
import './pronunciation.css';

export default function PronunciationButton({ text, label = '朗读', speechKey, compact = false, className = '', prepareOnMount = false }: {
  text: string; label?: string; speechKey?: string; compact?: boolean; className?: string; prepareOnMount?: boolean;
}) {
  const speech = useSpeech();
  const id = useId();
  const key = speechKey ?? `speech:${id}`;
  const active = speech.activeKey === key;
  const problem = speechTextProblem(text);
  const title = !speech.isSupported ? '当前浏览器不支持发音，请使用新版 Edge 或 Chrome。'
    : !speech.voiceAvailable ? '设备未提供所选英语口音，请在发音设置中切换口音或安装英语语音。'
      : problem || (active ? `停止${label}` : label);
  const { stop, prepare, cancelPreparation, neuralAvailable } = speech;
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearHover = () => { if (hoverTimer.current !== null) clearTimeout(hoverTimer.current); hoverTimer.current = null; };
  const prepareNow = () => { clearHover(); if (!document.hidden) prepare(text, { key }); };
  const cancelPrepare = () => { clearHover(); cancelPreparation(key); };
  useEffect(() => () => {
    if (hoverTimer.current !== null) clearTimeout(hoverTimer.current);
    cancelPreparation(key); stop(key);
  }, [stop, cancelPreparation, key, text]);
  useEffect(() => {
    if (!prepareOnMount || !neuralAvailable) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const onVisibility = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null; cancelPreparation(key);
      if (!document.hidden) timer = setTimeout(() => { timer = null; prepare(text, { key }); }, 600);
    };
    onVisibility(); document.addEventListener('visibilitychange', onVisibility);
    return () => {
      if (timer !== null) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility); cancelPreparation(key);
    };
  }, [prepareOnMount, neuralAvailable, prepare, cancelPreparation, key, text, speech.accent, speech.rate, speech.wordFallback]);
  return <button type="button" className={`pronunciation-button${active ? ' is-speaking' : ''}${compact ? ' pronunciation-button--compact' : ''} ${className}`}
    disabled={!speech.isSupported || !speech.voiceAvailable || Boolean(problem)} aria-pressed={active}
    aria-label={active ? `停止${label}` : label} title={title}
    onPointerEnter={event => { if (event.pointerType !== 'touch') { clearHover(); hoverTimer.current = setTimeout(prepareNow, 180); } }}
    onPointerLeave={cancelPrepare} onPointerDown={prepareNow} onPointerCancel={cancelPrepare}
    onFocus={prepareNow} onBlur={cancelPrepare}
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
      <label htmlFor={accentId}>首选口音<select id={accentId} aria-label="首选发音口音" disabled={!speech.isSupported} value={speech.accent} onChange={event => speech.setAccent(event.target.value as SpeechAccent)}><option value="en-US">美式英语</option><option value="en-GB">英式英语</option></select></label>
      <label htmlFor={rateId}>语速<select id={rateId} aria-label="朗读语速" disabled={!speech.isSupported} value={speech.rate} onChange={event => speech.setRate(Number(event.target.value) as SpeechRate)}><option value={0.8}>慢速 · 0.8×</option><option value={1}>正常 · 1×</option><option value={1.2}>快速 · 1.2×</option></select></label>
      {speech.rate !== 1 && <button type="button" className="speech-rate-reset" onClick={() => speech.setRate(1)}>恢复正常语速</button>}
    </div>
    <label className="speech-fallback-option"><input type="checkbox" checked={speech.wordFallback} onChange={event => speech.setWordFallback(event.target.checked)} />{speech.naturalAvailable ? '无录音时使用自然合成' : '无录音时使用合成朗读'}</label>
    {speech.naturalAvailable && <p className="speech-hint">开启后，缺录音单词使用 Rosie 美式合成声线。</p>}
    <p className="speech-hint" title="只准备当前单词，不会自动播放。句子在本机朗读。">{speech.wordFallback ? '首选口音缺录音时尝试另一口音；发音失败可重新点击。' : '单词仅播放词典录音；首选口音缺录音时尝试另一口音。'}</p>
    {speech.voiceName && <p className="speech-source" title={speech.engine === 'system' ? undefined : '已加首尾缓冲'}>{pronunciationSourceLabel(speech.source)}<span>·</span>{speech.voiceName}</p>}
    {speech.engine === 'dictionary' && speech.attribution && <p className="speech-attribution">
      <span>{speech.attribution.author} · {speech.attribution.license}</span>
      <a href={speech.attribution.source_url} target="_blank" rel="noopener noreferrer">Commons 来源</a>
      <a href={speech.attribution.license_url} target="_blank" rel="noopener noreferrer">许可</a>
      <span>已加首尾缓冲</span>
    </p>}
    <SpeechStatus />
  </div>;
}

export function SpeechStatus() {
  const speech = useSpeech();
  const unavailable = !speech.isSupported ? '当前浏览器不支持发音，请使用新版 Edge 或 Chrome。'
    : !speech.voiceAvailable ? `设备尚未提供${speech.accent === 'en-US' ? '美式' : '英式'}英语语音。可切换口音，或在系统设置中安装对应英语语音。` : '';
  return speech.error || unavailable || speech.notice ? <p className="speech-status" role="status">{speech.error || unavailable || speech.notice}</p> : null;
}
