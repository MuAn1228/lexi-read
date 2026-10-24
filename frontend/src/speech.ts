export type SpeechAccent = 'en-US' | 'en-GB';
export type SpeechRate = 0.8 | 1 | 1.2;
export type SpeechPreferences = { accent: SpeechAccent; rate: SpeechRate; wordFallback: boolean };
export type PronunciationAttribution = { author: string; license: string; source_url: string; license_url: string };
export type PronunciationSource = 'dictionary' | 'kitten' | 'piper';
export type PronunciationAudio = { blob: Blob; source: PronunciationSource; attribution?: PronunciationAttribution; accent?: SpeechAccent };
export type SpeechSnapshot = SpeechPreferences & {
  isSupported: boolean;
  status: 'idle' | 'starting' | 'speaking' | 'error';
  activeKey: string | null;
  error: string;
  notice: string;
  engine: 'dictionary' | 'neural' | 'system';
  source: PronunciationSource | 'system' | null;
  attribution: PronunciationAttribution | null;
  neuralAvailable: boolean;
  naturalAvailable: boolean;
  voiceAvailable: boolean;
  voiceName: string;
};
export const SPEECH_TEXT_LIMIT = 4_000;
const DEFAULTS: SpeechPreferences = { accent: 'en-US', rate: 1, wordFallback: false };
const RATES: SpeechRate[] = [0.8, 1, 1.2];
const SYSTEM_NOTICE = '当前使用系统英语语音，音色与基础语速受设备影响。';
const FALLBACK_NOTICE = '录音与本机合成暂不可用，已回退到系统英语语音；再次点击会重新尝试。';
const NO_RECORDING = '暂未找到可用的美式或英式词典录音。如需合成朗读，可在发音设置中开启。';
const RECORDING_FAILED = '词典录音暂时无法播放，请稍后重试或切换口音。未自动使用合成朗读。';
const WORD_FAILED = '单词发音暂时无法播放，请稍后重试。未自动使用系统语音。';

export function pronunciationSourceLabel(source: SpeechSnapshot['source']): string {
  return source === 'dictionary' ? '词典录音' : source === 'kitten' ? '本机神经语音'
    : source === 'piper' ? '本机合成 · Piper' : source === 'system' ? '系统语音' : '';
}

/** Match the backend's word normalization, including possessives and final punctuation. */
export function isSpeechWord(text: string): boolean {
  return /^[A-Za-z]+(?:[-'][A-Za-z]+)*'?$/.test(text.trim().replace(/[.!?]+$/, '').replace(/[‘’]/g, "'"));
}

export function readSpeechPreferences(raw: string | null): SpeechPreferences {
  try {
    const value: unknown = JSON.parse(raw ?? 'null');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return { ...DEFAULTS };
    const data = value as Record<string, unknown>;
    return {
      accent: data.accent === 'en-GB' ? 'en-GB' : 'en-US',
      rate: RATES.includes(data.rate as SpeechRate) ? data.rate as SpeechRate : 1,
      wordFallback: data.wordFallback === true,
    };
  } catch { return { ...DEFAULTS }; }
}

/** The recalibrated voice starts at normal speed while preserving the previous accent. */
export function restoreSpeechPreferences(currentRaw: string | null, legacyRaw: string | null): SpeechPreferences {
  if (currentRaw !== null) return readSpeechPreferences(currentRaw);
  return { accent: readSpeechPreferences(legacyRaw).accent, rate: 1, wordFallback: false };
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
  loadAudio?: (text: string, accent: SpeechAccent, signal: AbortSignal, wordFallback: boolean) => Promise<Blob | PronunciationAudio>;
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
  let preparation: { key: string; text: string; accent: SpeechAccent; wordFallback: boolean; controller: AbortController } | null = null;
  let sequence = 0;
  let watchdog: ReturnType<typeof setTimeout> | null = null;
  let snapshot: SpeechSnapshot = {
    ...DEFAULTS, ...preferences, isSupported: systemSupported || naturalReady, status: 'idle', activeKey: null,
    error: '', notice: naturalReady ? '' : systemNotice, engine: naturalReady ? 'neural' : 'system', source: naturalReady ? null : 'system', attribution: null,
    neuralAvailable: naturalReady, naturalAvailable: false, voiceAvailable: false, voiceName: '',
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
  const preferredVoice = () => naturalReady ? { engine: 'neural' as const, source: null, voiceName: '', attribution: null }
    : { engine: 'system' as const, source: 'system' as const, voiceName: selectSpeechVoice(voices, snapshot.accent)?.name ?? '', attribution: null };
  const cancelPreparation = (key?: string) => {
    if (!preparation || key !== undefined && preparation.key !== key) return;
    preparation.controller.abort(); preparation = null;
  };
  const stop = (key?: string) => {
    if (key !== undefined && key !== snapshot.activeKey) { cancelPreparation(key); return; }
    cancelPreparation();
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
    try { onPreferences?.({ accent: snapshot.accent, rate: snapshot.rate, wordFallback: snapshot.wordFallback }); } catch { /* Speech still works without storage. */ }
  };
  const startSystem = (text: string, key: string, token: number, fallback = false) => {
    if (token !== sequence) return;
    if (isSpeechWord(text)) {
      fail('单词发音服务暂不可用，请稍后重试。未自动使用系统合成朗读。'); return;
    }
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
      update({ activeKey: key, status: 'starting', error: '', engine: 'system', source: 'system', voiceName: voice.name, attribution: null, notice: fallback ? FALLBACK_NOTICE : systemNotice });
      watchdog = setTimeout(() => { if (isCurrent()) fail('系统语音没有响应，请检查英语语音设置后重试。'); }, 12_000);
      if (synthesis.paused) synthesis.resume();
      synthesis.speak(utterance);
    } catch { fail('无法启动英语发音，请检查浏览器和系统语音设置后重试。'); }
  };
  const startNatural = async (text: string, key: string, token: number) => {
    if (!loadAudio || !createAudio || !createObjectURL) return;
    const request = new AbortController(); pending = request;
    const singleWord = isSpeechWord(text);
    const dictionaryOnly = singleWord && !snapshot.wordFallback;
    const isCurrent = () => token === sequence && !request.signal.aborted;
    update({ activeKey: key, status: 'starting', error: '', notice: '', ...preferredVoice() });
    watchdog = setTimeout(() => { if (isCurrent()) fail('发音准备超时，请稍后再次点击播放。'); }, 180_000);
    try {
      const result = await loadAudio(text, snapshot.accent, request.signal, snapshot.wordFallback);
      if (!isCurrent()) return;
      const recording = result instanceof Blob ? { blob: result, source: 'piper' as const } : result;
      const { blob, source, attribution } = recording;
      const actualAccent = recording.accent === 'en-GB' || recording.accent === 'en-US' ? recording.accent : snapshot.accent;
      if (dictionaryOnly && source !== 'dictionary') { fail(NO_RECORDING); return; }
      if (source !== 'dictionary' && source !== 'piper' && source !== 'kitten' || source === 'dictionary' && !attribution
        || source === 'kitten' && recording.accent !== 'en-US') throw new Error('Unverified pronunciation source');
      if (!blob.size) throw new Error('Empty pronunciation audio');
      const url = createObjectURL(blob); audioURL = url;
      const player = createAudio(url); audio = player;
      player.playbackRate = snapshot.rate; player.preservesPitch = true;
      update({ engine: source === 'dictionary' ? 'dictionary' : 'neural', source,
        voiceName: source === 'kitten' ? 'Rosie（美式）' : actualAccent === 'en-US' ? '美式英语' : '英式英语',
        attribution: source === 'dictionary' ? attribution ?? null : null,
        notice: source === 'dictionary' && actualAccent !== snapshot.accent
          ? `暂无${snapshot.accent === 'en-US' ? '美式' : '英式'}录音，播放${actualAccent === 'en-US' ? '美式' : '英式'}词典录音。`
          : source === 'kitten' ? `${singleWord ? '此词暂无可用的词典录音，' : ''}使用 Rosie 美式合成声线。`
            : source === 'piper' && singleWord ? '此词暂无可用的词典录音，使用本机合成。' : '' });
      const isPlayerCurrent = () => isCurrent() && audio === player;
      const onPlaying = () => {
        if (!isPlayerCurrent()) { try { player.pause(); } catch { /* Ignore a stopped device. */ } return; }
        clearWatchdog(); update({ status: 'speaking' });
        watchdog = setTimeout(() => { if (isPlayerCurrent()) fail('朗读已中断，请再次点击播放。'); }, Math.max(30_000, text.length * 200 / snapshot.rate));
      };
      const fallback = () => {
        if (!isPlayerCurrent()) return;
        if (singleWord) { fail(dictionaryOnly ? RECORDING_FAILED : WORD_FAILED); return; }
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
    } catch (error) {
      if (!isCurrent()) return;
      if (dictionaryOnly) {
        const missing = error !== null && typeof error === 'object' && 'code' in error && error.code === 'recording_not_found';
        fail(missing ? NO_RECORDING : RECORDING_FAILED); return;
      }
      if (singleWord) { fail(WORD_FAILED); return; }
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
    cancelPreparation,
    prepare: (text: string, options: { key?: string } = {}) => {
      const word = text.trim();
      if (!naturalReady || !loadAudio || word.length > 80 || !isSpeechWord(word)) return;
      const key = options.key ?? word;
      if (preparation?.key === key && preparation.text === word && preparation.accent === snapshot.accent && preparation.wordFallback === snapshot.wordFallback) return;
      cancelPreparation();
      const request = { key, text: word, accent: snapshot.accent, wordFallback: snapshot.wordFallback, controller: new AbortController() };
      preparation = request;
      // Preparation never changes playback state, creates an audio element, or falls back to system speech.
      void loadAudio(word, request.accent, request.controller.signal, request.wordFallback).catch(() => undefined)
        .finally(() => { if (preparation === request) preparation = null; });
    },
    setNeuralAvailable: (available: boolean, reason?: string | null, naturalAvailable = false) => {
      naturalReady = available && audioSupported;
      update({ naturalAvailable: naturalReady && naturalAvailable === true });
      if (!naturalReady) cancelPreparation();
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
    setWordFallback: (enabled: boolean) => {
      if (typeof enabled !== 'boolean' || enabled === snapshot.wordFallback) return;
      stop(); update({ wordFallback: enabled }); savePreferences();
    },
    speak: (text: string, options: { key?: string } = {}) => {
      const problem = speechTextProblem(text);
      if (problem) { fail(problem); return; }
      refreshVoices(); cancel();
      if (preparation && (preparation.text !== text.trim() || preparation.accent !== snapshot.accent)) cancelPreparation();
      const key = options.key ?? text;
      if (naturalReady) void startNatural(text.trim(), key, sequence);
      else startSystem(text.trim(), key, sequence);
    },
  };
}

export type SpeechController = ReturnType<typeof createSpeechController>;
