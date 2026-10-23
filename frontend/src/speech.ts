export type SpeechAccent = 'en-US' | 'en-GB';
export type SpeechRate = 0.8 | 1 | 1.2;
export type SpeechPreferences = { accent: SpeechAccent; rate: SpeechRate };
export type SpeechSnapshot = SpeechPreferences & {
  isSupported: boolean;
  status: 'idle' | 'starting' | 'speaking' | 'error';
  activeKey: string | null;
  error: string;
  voiceAvailable: boolean;
  voiceName: string;
};
export const SPEECH_TEXT_LIMIT = 4_000;
const DEFAULTS: SpeechPreferences = { accent: 'en-US', rate: 1 };
const RATES: SpeechRate[] = [0.8, 1, 1.2];

export function readSpeechPreferences(raw: string | null): SpeechPreferences {
  try {
    const value: unknown = JSON.parse(raw ?? 'null');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return { ...DEFAULTS };
    const data = value as Record<string, unknown>;
    return {
      accent: data.accent === 'en-GB' ? 'en-GB' : 'en-US',
      rate: RATES.includes(data.rate as SpeechRate) ? data.rate as SpeechRate : 1,
    };
  } catch { return { ...DEFAULTS }; }
}

/** Never silently substitute another accent or a non-English system default. */
export function selectSpeechVoice(voices: readonly SpeechSynthesisVoice[], accent: SpeechAccent) {
  const matches = voices.filter(voice => voice.lang.replaceAll('_', '-').toLowerCase() === accent.toLowerCase());
  return matches.find(voice => voice.localService && voice.default)
    ?? matches.find(voice => voice.localService) ?? matches.find(voice => voice.default) ?? matches[0] ?? null;
}

export function speechTextProblem(text: string): string {
  if (!text.trim()) return '当前没有可朗读的英文内容。';
  if (Array.from(text).length > SPEECH_TEXT_LIMIT) return '这段内容超过 4,000 字符，请选择较短的句子。';
  return '';
}

type SpeechEngine = Pick<SpeechSynthesis, 'getVoices' | 'speak' | 'cancel' | 'resume' | 'paused' | 'addEventListener' | 'removeEventListener'>;
type ControllerOptions = {
  synthesis: SpeechEngine | null;
  createUtterance: ((text: string) => SpeechSynthesisUtterance) | null;
  preferences?: SpeechPreferences;
  onPreferences?: (value: SpeechPreferences) => void;
};

/** One controller owns the whole app's speech queue. Generation checks ignore late browser callbacks. */
export function createSpeechController({ synthesis, createUtterance, preferences = DEFAULTS, onPreferences }: ControllerOptions) {
  const isSupported = Boolean(synthesis && createUtterance);
  let voices: SpeechSynthesisVoice[] = [];
  let current: SpeechSynthesisUtterance | null = null;
  let sequence = 0;
  let watchdog: ReturnType<typeof setTimeout> | null = null;
  let snapshot: SpeechSnapshot = {
    ...preferences, isSupported, status: 'idle', activeKey: null,
    error: '', voiceAvailable: false, voiceName: '',
  };
  const listeners = new Set<() => void>();
  const update = (patch: Partial<SpeechSnapshot>) => {
    snapshot = { ...snapshot, ...patch };
    listeners.forEach(listener => listener());
  };
  const clearWatchdog = () => { if (watchdog !== null) clearTimeout(watchdog); watchdog = null; };
  const detach = () => {
    clearWatchdog();
    if (current) { current.onstart = null; current.onend = null; current.onerror = null; current = null; }
  };
  const cancel = () => {
    ++sequence; detach();
    try { synthesis?.cancel(); } catch { /* Some embedded browsers expose a non-working speech engine. */ }
  };
  const stop = (key?: string) => {
    if (key !== undefined && key !== snapshot.activeKey) return;
    cancel(); update({ activeKey: null, status: 'idle', error: '' });
  };
  const refreshVoices = () => {
    try { voices = synthesis?.getVoices() ?? []; } catch { voices = []; }
    const voice = selectSpeechVoice(voices, snapshot.accent);
    if (!voice && current) stop();
    update({ voiceAvailable: Boolean(voice), voiceName: voice?.name ?? '' });
  };
  refreshVoices();
  const fail = (message: string) => { cancel(); update({ activeKey: null, status: 'error', error: message }); };
  const savePreferences = () => {
    try { onPreferences?.({ accent: snapshot.accent, rate: snapshot.rate }); } catch { /* Speech still works without storage. */ }
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    connect: () => {
      synthesis?.addEventListener('voiceschanged', refreshVoices);
      refreshVoices();
      // Reconnectable cleanup is intentional: React StrictMode runs setup/cleanup twice.
      return () => { synthesis?.removeEventListener('voiceschanged', refreshVoices); stop(); };
    },
    stop,
    setAccent: (accent: SpeechAccent) => {
      if (!['en-US', 'en-GB'].includes(accent) || accent === snapshot.accent) return;
      stop(); update({ accent }); refreshVoices(); savePreferences();
    },
    setRate: (rate: SpeechRate) => {
      if (!RATES.includes(rate) || rate === snapshot.rate) return;
      stop(); update({ rate }); savePreferences();
    },
    speak: (text: string, options: { key?: string } = {}) => {
      if (!isSupported || !synthesis || !createUtterance) {
        fail('当前浏览器不支持发音，请使用新版 Edge 或 Chrome。'); return;
      }
      const problem = speechTextProblem(text);
      if (problem) { fail(problem); return; }
      refreshVoices();
      const voice = selectSpeechVoice(voices, snapshot.accent);
      if (!voice) { fail('当前设备没有所选口音的英语语音。请切换口音，或在系统设置中安装英语语音。'); return; }
      cancel();
      const token = sequence;
      try {
        const utterance = createUtterance(text.trim());
        current = utterance;
        utterance.lang = snapshot.accent; utterance.voice = voice; utterance.rate = snapshot.rate;
        const key = options.key ?? text;
        const isCurrent = () => token === sequence && current === utterance;
        utterance.onstart = () => {
          if (!isCurrent()) return;
          clearWatchdog(); update({ status: 'speaking' });
          // Recover if a browser silently drops its final event during a device change.
          watchdog = setTimeout(() => { if (isCurrent()) fail('朗读已中断，请再次点击播放。'); }, Math.max(30_000, text.length * 200 / snapshot.rate));
        };
        utterance.onend = () => {
          if (!isCurrent()) return;
          detach(); update({ activeKey: null, status: 'idle', error: '' });
        };
        utterance.onerror = event => {
          if (!isCurrent()) return;
          if (event.error === 'canceled' || event.error === 'interrupted') { stop(); return; }
          const message = event.error === 'not-allowed' ? '浏览器阻止了发音，请直接点击朗读按钮重试。'
            : event.error === 'network' ? '系统语音服务连接失败，请检查网络或改用已安装的英语语音。'
              : '英语发音暂不可用，请检查系统英语语音与音频输出后重试。';
          fail(message);
        };
        update({ activeKey: key, status: 'starting', error: '' });
        watchdog = setTimeout(() => { if (isCurrent()) fail('系统语音没有响应，请检查英语语音设置后重试。'); }, 12_000);
        if (synthesis.paused) synthesis.resume();
        synthesis.speak(utterance);
      } catch { fail('无法启动英语发音，请检查浏览器和系统语音设置后重试。'); }
    },
  };
}

export type SpeechController = ReturnType<typeof createSpeechController>;
