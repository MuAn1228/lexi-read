export type SpeechAccent = 'en-US' | 'en-GB';
export type SpeechRate = 0.8 | 1 | 1.2;
export type SpeechPreferences = { accent: SpeechAccent; rate: SpeechRate };
export type PronunciationAttribution = { author: string; license: string; source_url: string; license_url: string };
export type PronunciationAudio = { blob: Blob; source: 'dictionary' | 'piper'; attribution?: PronunciationAttribution };
export type SpeechSnapshot = SpeechPreferences & {
  isSupported: boolean;
  status: 'idle' | 'starting' | 'speaking' | 'error';
  activeKey: string | null;
  error: string;
  notice: string;
  engine: 'dictionary' | 'neural' | 'system';
  attribution: PronunciationAttribution | null;
  neuralAvailable: boolean;
  voiceAvailable: boolean;
  voiceName: string;
};
export const SPEECH_TEXT_LIMIT = 4_000;
const DEFAULTS: SpeechPreferences = { accent: 'en-US', rate: 1 };
const RATES: SpeechRate[] = [0.8, 1, 1.2];
const SYSTEM_NOTICE = '当前使用系统英语语音，音色与基础语速受设备影响。';
const FALLBACK_NOTICE = '录音与本机合成暂不可用，已回退到系统英语语音；再次点击会重新尝试。';

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

/** The recalibrated voice starts at normal speed while preserving the previous accent. */
export function restoreSpeechPreferences(currentRaw: string | null, legacyRaw: string | null): SpeechPreferences {
  if (currentRaw !== null) return readSpeechPreferences(currentRaw);
  return { accent: readSpeechPreferences(legacyRaw).accent, rate: 1 };
}

/** Keep the requested accent; never substitute a Chinese default or another English accent. */
export function selectSpeechVoice(voices: readonly SpeechSynthesisVoice[], accent: SpeechAccent) {
  const matches = voices.filter(voice => voice.lang.replaceAll('_', '-').toLowerCase() === accent.toLowerCase());
  const natural = (voice: SpeechSynthesisVoice) => /natural|neural/i.test(voice.name);
  return matches.find(voice => natural(voice) && voice.localService)
    ?? matches.find(natural)
    ?? matches.find(voice => voice.localService && /\bzira\b/i.test(voice.name))
    ?? matches.find(voice => voice.localService && voice.default)
    ?? matches.find(voice => voice.localService) ?? matches.find(voice => voice.default) ?? matches[0] ?? null;
}

export function speechTextProblem(text: string): string {
  if (!text.trim()) return '当前没有可朗读的英文内容。';
  if (Array.from(text).length > SPEECH_TEXT_LIMIT) return '这段内容超过 4,000 字符，请选择较短的句子。';
  return '';
}

type SpeechEngine = Pick<SpeechSynthesis, 'getVoices' | 'speak' | 'cancel' | 'resume' | 'paused' | 'addEventListener' | 'removeEventListener'>;
type SpeechAudio = Pick<HTMLAudioElement, 'play' | 'pause' | 'src' | 'playbackRate' | 'preservesPitch' | 'onplaying' | 'onended' | 'onerror'>;
type ControllerOptions = {
  synthesis: SpeechEngine | null;
  createUtterance: ((text: string) => SpeechSynthesisUtterance) | null;
  preferences?: SpeechPreferences;
  onPreferences?: (value: SpeechPreferences) => void;
  loadAudio?: (text: string, accent: SpeechAccent, signal: AbortSignal) => Promise<Blob | PronunciationAudio>;
  createAudio?: (url: string) => SpeechAudio;
  createObjectURL?: (blob: Blob) => string;
  revokeObjectURL?: (url: string) => void;
  neuralAvailable?: boolean;
};

/** One owner cancels both playback engines, including requests and late browser callbacks. */
export function createSpeechController({ synthesis, createUtterance, preferences = DEFAULTS, onPreferences, loadAudio, createAudio, createObjectURL, revokeObjectURL, neuralAvailable = false }: ControllerOptions) {
  const systemSupported = Boolean(synthesis && createUtterance);
  const audioSupported = Boolean(loadAudio && createAudio && createObjectURL && revokeObjectURL);
  let naturalReady = neuralAvailable && audioSupported;
  let systemNotice = SYSTEM_NOTICE;
  let voices: SpeechSynthesisVoice[] = [];
  let current: SpeechSynthesisUtterance | null = null;
  let audio: SpeechAudio | null = null;
  let audioURL: string | null = null;
  let pending: AbortController | null = null;
  let sequence = 0;
  let watchdog: ReturnType<typeof setTimeout> | null = null;
  let snapshot: SpeechSnapshot = {
    ...preferences, isSupported: systemSupported || naturalReady, status: 'idle', activeKey: null,
    error: '', notice: naturalReady ? '' : systemNotice, engine: naturalReady ? 'neural' : 'system', attribution: null,
    neuralAvailable: naturalReady, voiceAvailable: false, voiceName: '',
  };
  const listeners = new Set<() => void>();
  const update = (patch: Partial<SpeechSnapshot>) => {
    snapshot = { ...snapshot, ...patch };
    listeners.forEach(listener => listener());
  };
  const clearWatchdog = () => { if (watchdog !== null) clearTimeout(watchdog); watchdog = null; };
  const detach = () => {
    clearWatchdog();
    pending?.abort(); pending = null;
    if (current) { current.onstart = null; current.onend = null; current.onerror = null; current = null; }
    if (audio) {
      audio.onplaying = null; audio.onended = null; audio.onerror = null;
      try { audio.pause(); audio.src = ''; } catch { /* A detached device must not prevent URL cleanup. */ }
      audio = null;
    }
    if (audioURL !== null) { try { revokeObjectURL?.(audioURL); } catch { /* Cleanup is best effort. */ } audioURL = null; }
  };
  const cancel = () => {
    ++sequence; detach();
    try { synthesis?.cancel(); } catch { /* Some embedded browsers expose a non-working speech engine. */ }
  };
  const preferredVoice = () => naturalReady ? { engine: 'neural' as const, voiceName: '', attribution: null }
    : { engine: 'system' as const, voiceName: selectSpeechVoice(voices, snapshot.accent)?.name ?? '', attribution: null };
  const stop = (key?: string) => {
    if (key !== undefined && key !== snapshot.activeKey) return;
    cancel(); update({ activeKey: null, status: 'idle', error: '', notice: naturalReady ? '' : systemNotice, ...preferredVoice() });
  };
  const refreshVoices = () => {
    try { voices = synthesis?.getVoices() ?? []; } catch { voices = []; }
    const voice = selectSpeechVoice(voices, snapshot.accent);
    if (!voice && current) stop();
    update({ isSupported: systemSupported || naturalReady, neuralAvailable: naturalReady,
      voiceAvailable: naturalReady || Boolean(voice), ...(snapshot.activeKey === null ? preferredVoice() : {}) });
  };
  refreshVoices();
  const fail = (message: string) => { cancel(); update({ activeKey: null, status: 'error', error: message, ...preferredVoice() }); };
  const savePreferences = () => {
    try { onPreferences?.({ accent: snapshot.accent, rate: snapshot.rate }); } catch { /* Speech still works without storage. */ }
  };
  const startSystem = (text: string, key: string, token: number, fallback = false) => {
    if (token !== sequence) return;
    const voice = selectSpeechVoice(voices, snapshot.accent);
    if (!systemSupported || !synthesis || !createUtterance || !voice) {
      fail(fallback ? '录音与本机合成暂不可用，设备也没有所选口音的系统英语语音。请稍后重试或切换口音。'
        : !systemSupported ? '当前浏览器不支持发音，请使用新版 Edge 或 Chrome。'
          : '当前设备没有所选口音的英语语音。请切换口音，或在系统设置中安装英语语音。'); return;
    }
    try {
      const utterance = createUtterance(text);
      current = utterance;
      utterance.lang = snapshot.accent; utterance.voice = voice;
      utterance.rate = snapshot.rate; utterance.pitch = 1; utterance.volume = 1;
      const isCurrent = () => token === sequence && current === utterance;
      utterance.onstart = () => {
        if (!isCurrent()) return;
        clearWatchdog(); update({ status: 'speaking' });
        watchdog = setTimeout(() => { if (isCurrent()) fail('朗读已中断，请再次点击播放。'); }, Math.max(30_000, text.length * 200 / snapshot.rate));
      };
      utterance.onend = () => {
        if (!isCurrent()) return;
        detach(); update({ activeKey: null, status: 'idle', error: '' });
      };
      utterance.onerror = event => {
        if (!isCurrent()) return;
        if (event.error === 'canceled' || event.error === 'interrupted') { stop(); return; }
        fail(event.error === 'not-allowed' ? '浏览器阻止了发音，请直接点击朗读按钮重试。'
          : event.error === 'network' ? '系统语音服务连接失败，请检查网络或改用已安装的英语语音。'
            : '英语发音暂不可用，请检查系统英语语音与音频输出后重试。');
      };
      update({ activeKey: key, status: 'starting', error: '', engine: 'system', voiceName: voice.name, attribution: null, notice: fallback ? FALLBACK_NOTICE : systemNotice });
      watchdog = setTimeout(() => { if (isCurrent()) fail('系统语音没有响应，请检查英语语音设置后重试。'); }, 12_000);
      if (synthesis.paused) synthesis.resume();
      synthesis.speak(utterance);
    } catch { fail('无法启动英语发音，请检查浏览器和系统语音设置后重试。'); }
  };
  const startNatural = async (text: string, key: string, token: number) => {
    if (!loadAudio || !createAudio || !createObjectURL) return;
    const request = new AbortController(); pending = request;
    const isCurrent = () => token === sequence && !request.signal.aborted;
    update({ activeKey: key, status: 'starting', error: '', notice: '', ...preferredVoice() });
    watchdog = setTimeout(() => { if (isCurrent()) fail('发音准备超时，请稍后再次点击播放。'); }, 180_000);
    try {
      const result = await loadAudio(text, snapshot.accent, request.signal);
      if (!isCurrent()) return;
      const recording = result instanceof Blob ? { blob: result, source: 'piper' as const } : result;
      const { blob, source, attribution } = recording;
      if (source !== 'dictionary' && source !== 'piper' || source === 'dictionary' && !attribution) throw new Error('Unverified pronunciation source');
      if (!blob.size) throw new Error('Empty pronunciation audio');
      const url = createObjectURL(blob); audioURL = url;
      const player = createAudio(url); audio = player;
      player.playbackRate = snapshot.rate; player.preservesPitch = true;
      update({ engine: source === 'dictionary' ? 'dictionary' : 'neural',
        voiceName: snapshot.accent === 'en-US' ? '美式英语' : '英式英语',
        attribution: source === 'dictionary' ? attribution ?? null : null,
        notice: source === 'piper' && /^[A-Za-z]+(?:['’\-][A-Za-z]+)*$/.test(text)
          ? '此词暂无可用的所选口音录音，使用本机合成。' : '' });
      const isPlayerCurrent = () => isCurrent() && audio === player;
      const onPlaying = () => {
        if (!isPlayerCurrent()) { try { player.pause(); } catch { /* Ignore a stopped device. */ } return; }
        clearWatchdog(); update({ status: 'speaking' });
        watchdog = setTimeout(() => { if (isPlayerCurrent()) fail('朗读已中断，请再次点击播放。'); }, Math.max(30_000, text.length * 200 / snapshot.rate));
      };
      const fallback = () => {
        if (!isPlayerCurrent()) return;
        detach(); startSystem(text, key, token, true);
      };
      player.onplaying = onPlaying;
      player.onended = () => {
        if (!isPlayerCurrent()) return;
        detach(); update({ activeKey: null, status: 'idle', error: '' });
      };
      player.onerror = fallback;
      try {
        await player.play();
        if (!isPlayerCurrent()) { try { player.pause(); } catch { /* Never revive canceled playback. */ } return; }
        if (snapshot.status === 'starting') onPlaying();
      } catch (error) {
        if (!isPlayerCurrent()) { try { player.pause(); } catch { /* A canceled play promise may settle late. */ } return; }
        if (error instanceof Error && error.name === 'NotAllowedError') fail('浏览器阻止了音频播放，请直接点击朗读按钮重试。');
        else fallback();
      }
    } catch {
      if (!isCurrent()) return;
      detach(); startSystem(text, key, token, true);
    }
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    connect: () => {
      synthesis?.addEventListener('voiceschanged', refreshVoices); refreshVoices();
      return () => { synthesis?.removeEventListener('voiceschanged', refreshVoices); stop(); };
    },
    stop,
    setNeuralAvailable: (available: boolean, reason?: string | null) => {
      naturalReady = available && audioSupported;
      systemNotice = reason ? reason + ' ' + SYSTEM_NOTICE : SYSTEM_NOTICE;
      refreshVoices();
      if (snapshot.activeKey === null) update({ notice: naturalReady ? '' : systemNotice });
    },
    setAccent: (accent: SpeechAccent) => {
      if (!['en-US', 'en-GB'].includes(accent) || accent === snapshot.accent) return;
      stop(); update({ accent }); refreshVoices(); savePreferences();
    },
    setRate: (rate: SpeechRate) => {
      if (!RATES.includes(rate) || rate === snapshot.rate) return;
      stop(); update({ rate }); savePreferences();
    },
    speak: (text: string, options: { key?: string } = {}) => {
      const problem = speechTextProblem(text);
      if (problem) { fail(problem); return; }
      refreshVoices(); cancel();
      const key = options.key ?? text;
      if (naturalReady) void startNatural(text.trim(), key, sequence);
      else startSystem(text.trim(), key, sequence);
    },
  };
}

export type SpeechController = ReturnType<typeof createSpeechController>;
