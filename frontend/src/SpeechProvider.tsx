import { createContext, useContext, useEffect, useState, useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';
import { browserStorageKey, request, requestPronunciation } from './api';
import { createSpeechController, restoreSpeechPreferences } from './speech';
import type { SpeechController } from './speech';

const SpeechContext = createContext<SpeechController | null>(null);

export function SpeechProvider({ children }: { children: ReactNode }) {
  const [controller] = useState(() => {
    let preferences = restoreSpeechPreferences(null, null);
    try {
      const preferenceKey = browserStorageKey('speech:v2');
      const current = localStorage.getItem(preferenceKey);
      preferences = restoreSpeechPreferences(current, current === null ? localStorage.getItem(browserStorageKey('speech:v1')) : null);
      if (current === null) localStorage.setItem(preferenceKey, JSON.stringify(preferences));
    } catch { /* Optional preference only. */ }
    const supported = typeof window !== 'undefined' && 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window;
    return createSpeechController({
      synthesis: supported ? window.speechSynthesis : null,
      createUtterance: supported ? text => new SpeechSynthesisUtterance(text) : null,
      preferences,
      onPreferences: value => { localStorage.setItem(browserStorageKey('speech:v2'), JSON.stringify(value)); },
      loadAudio: requestPronunciation,
      createAudio: typeof Audio === 'undefined' ? undefined : url => new Audio(url),
      createObjectURL: typeof URL.createObjectURL === 'function' ? blob => URL.createObjectURL(blob) : undefined,
      revokeObjectURL: typeof URL.revokeObjectURL === 'function' ? url => URL.revokeObjectURL(url) : undefined,
    });
  });
  useEffect(() => {
    const abort = new AbortController();
    request<{ available: boolean; reason: string | null }>('/pronunciation/status', { signal: abort.signal })
      .then(status => { if (!abort.signal.aborted) controller.setNeuralAvailable(status.available === true, status.reason); })
      .catch(() => { if (!abort.signal.aborted) controller.setNeuralAvailable(false, '暂时无法连接发音服务。'); });
    return () => abort.abort();
  }, [controller]);
  useEffect(() => {
    const disconnect = controller.connect();
    const onVisibility = () => { if (document.hidden) controller.stop(); };
    const onPageHide = () => controller.stop();
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', onPageHide);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', onPageHide);
      disconnect();
    };
  }, [controller]);
  return <SpeechContext.Provider value={controller}>{children}</SpeechContext.Provider>;
}

export function useSpeech() {
  const controller = useContext(SpeechContext);
  if (!controller) throw new Error('Speech components require SpeechProvider.');
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  return { ...snapshot, speak: controller.speak, stop: controller.stop, setAccent: controller.setAccent, setRate: controller.setRate };
}
