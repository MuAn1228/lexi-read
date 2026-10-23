import { createContext, useContext, useEffect, useState, useSyncExternalStore } from 'react';
import type { ReactNode } from 'react';
import { browserStorageKey } from './api';
import { createSpeechController, readSpeechPreferences } from './speech';
import type { SpeechController } from './speech';

const SpeechContext = createContext<SpeechController | null>(null);

export function SpeechProvider({ children }: { children: ReactNode }) {
  const [controller] = useState(() => {
    let preferences = readSpeechPreferences(null);
    try { preferences = readSpeechPreferences(localStorage.getItem(browserStorageKey('speech:v1'))); } catch { /* Optional preference only. */ }
    const supported = typeof window !== 'undefined' && 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window;
    return createSpeechController({
      synthesis: supported ? window.speechSynthesis : null,
      createUtterance: supported ? text => new SpeechSynthesisUtterance(text) : null,
      preferences,
      onPreferences: value => { localStorage.setItem(browserStorageKey('speech:v1'), JSON.stringify(value)); },
    });
  });
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
