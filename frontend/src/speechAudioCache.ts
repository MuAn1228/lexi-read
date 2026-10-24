import type { PronunciationAudio, SpeechAccent } from './speech';
import { isSpeechWord } from './speech';

type AudioLoader = (text: string, accent: SpeechAccent, signal: AbortSignal, wordFallback: boolean) => Promise<PronunciationAudio>;
type CacheOptions = {
  load: AudioLoader;
  getScope: () => Promise<string>;
  now?: () => number;
  maxEntries?: number;
  maxBytes?: number;
};
type CachedAudio = { audio: PronunciationAudio; expires: number };
type PendingAudio = { controller: AbortController; consumers: Set<symbol>; promise: Promise<PronunciationAudio> };
const DICTIONARY_TTL = 30 * 60_000;
const SYNTHESIS_TTL = 15_000;
const abortError = () => new DOMException('Pronunciation request canceled', 'AbortError');
const copy = (audio: PronunciationAudio): PronunciationAudio => ({ ...audio,
  ...(audio.attribution ? { attribution: { ...audio.attribution } } : {}) });

/** A bounded, identity-scoped page-session cache; it never persists learning text or audio. */
export function createPronunciationCache({ load, getScope, now = Date.now, maxEntries = 64, maxBytes = 8 * 1024 * 1024 }: CacheOptions) {
  const cache = new Map<string, CachedAudio>();
  const pending = new Map<string, PendingAudio>();
  let scope: string | null = null;
  let generation = 0;
  let bytes = 0;
  const remove = (key: string) => {
    const entry = cache.get(key);
    if (entry) { bytes -= entry.audio.blob.size; cache.delete(key); }
  };
  const clear = () => {
    ++generation;
    cache.clear(); bytes = 0;
    pending.forEach(entry => entry.controller.abort()); pending.clear();
  };
  const purge = () => {
    for (const [key, entry] of cache) if (entry.expires <= now()) remove(key);
  };
  const remember = (key: string, audio: PronunciationAudio) => {
    purge(); remove(key);
    if (audio.blob.size > maxBytes || maxEntries < 1) return;
    cache.set(key, { audio: copy(audio), expires: now() + (audio.source === 'dictionary' ? DICTIONARY_TTL : SYNTHESIS_TTL) });
    bytes += audio.blob.size;
    while (cache.size > maxEntries || bytes > maxBytes) remove(cache.keys().next().value!);
  };
  const consume = (entry: PendingAudio, signal: AbortSignal): Promise<PronunciationAudio> => new Promise((resolve, reject) => {
    const consumer = Symbol();
    let settled = false;
    const finish = () => {
      settled = true; signal.removeEventListener('abort', onAbort); entry.consumers.delete(consumer);
    };
    const onAbort = () => {
      if (settled) return;
      finish();
      if (!entry.consumers.size) entry.controller.abort();
      reject(abortError());
    };
    if (signal.aborted) { onAbort(); return; }
    entry.consumers.add(consumer);
    signal.addEventListener('abort', onAbort, { once: true });
    entry.promise.then(audio => {
      if (settled) return;
      finish(); resolve(copy(audio));
    }, error => {
      if (settled) return;
      finish(); reject(error);
    });
  });
  return {
    clear,
    load: async (text: string, accent: SpeechAccent, signal: AbortSignal, wordFallback = false): Promise<PronunciationAudio> => {
      signal.throwIfAborted();
      const identity = await getScope();
      signal.throwIfAborted();
      if (scope !== identity) { clear(); scope = identity; }
      purge();
      const normalized = text.trim();
      const key = JSON.stringify([accent, normalized, wordFallback]);
      const cached = cache.get(key);
      if (cached) {
        cache.delete(key); cache.set(key, cached);
        return copy(cached.audio);
      }
      let entry = pending.get(key);
      if (!entry || entry.controller.signal.aborted) {
        const controller = new AbortController();
        const currentGeneration = generation;
        const fresh: PendingAudio = { controller, consumers: new Set(), promise: Promise.resolve().then(() => {
          controller.signal.throwIfAborted();
          return load(normalized, accent, controller.signal, wordFallback);
        }).then(audio => {
          controller.signal.throwIfAborted();
          if (!wordFallback && isSpeechWord(normalized) && audio.source !== 'dictionary') {
            throw Object.assign(new Error('No dictionary recording available'), { code: 'recording_not_found' });
          }
          if (!audio.blob.size || !['dictionary', 'piper'].includes(audio.source)
            || audio.source === 'dictionary' && !audio.attribution) throw new Error('Invalid pronunciation audio');
          if (currentGeneration !== generation) throw abortError();
          if (fresh.consumers.size) remember(key, audio);
          return audio;
        }).finally(() => { if (pending.get(key) === fresh) pending.delete(key); }) };
        pending.set(key, fresh); entry = fresh;
      }
      return consume(entry, signal);
    },
  };
}
