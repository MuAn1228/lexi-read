import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { MascotApiContext, MascotSnapshotContext } from './useMascot';
import { createMascotRuntime, defaultMascotConfig, mascotCurrentState, mascotNextExpiry, MASCOT_STORAGE_KEY, normalizeMascotConfig, parseMascotConfig, reduceMascot } from './mascotState';
import type { MascotApi, MascotConfig, MascotSnapshot } from './mascotTypes';

function readConfig(): MascotConfig {
  try { return parseMascotConfig(window.localStorage.getItem(MASCOT_STORAGE_KEY)); }
  catch { return { ...defaultMascotConfig }; }
}

export function MascotProvider({ children }: { children: ReactNode }) {
  const [runtime, dispatch] = useReducer(reduceMascot, undefined, createMascotRuntime);
  const [config, setConfig] = useState(readConfig);
  const configRef = useRef(config);
  configRef.current = config;
  const [suppressed, setSuppressed] = useState(false);

  useEffect(() => {
    try { window.localStorage.setItem(MASCOT_STORAGE_KEY, JSON.stringify(config)); }
    catch { /* Private browsing or full storage must never interrupt reading. */ }
  }, [config]);

  const expires = mascotNextExpiry(runtime);
  useEffect(() => {
    if (expires === null) return;
    const timeout = window.setTimeout(() => dispatch({ type: 'tick', now: Date.now() }), Math.max(0, expires - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [expires]);

  const api = useMemo<MascotApi>(() => {
    const updateConfig = (patch: Partial<MascotConfig>) => {
      setConfig(previous => normalizeMascotConfig({ ...previous, ...patch }));
    };
    return {
      emit: event => {
        // An optional presentation layer must never throw into an existing API flow.
        try { dispatch({ type: 'event', event, now: Date.now(), sample: Math.random(), choice: Math.random() }); }
        catch { /* Keep the confirmed business action intact. */ }
      },
      cancelOperation: operationId => dispatch({ type: 'cancel', operationId, now: Date.now() }),
      setEnvironment: value => setSuppressed(value.suppressed),
      updateConfig,
      resetPosition: () => updateConfig({ x: null, y: null, anchor: 'right', side: 'right' }),
      show: () => updateConfig({ hidden: false }),
      hide: () => updateConfig({ hidden: true }),
      interact: () => dispatch({ type: 'interact', now: Date.now(), choice: Math.random(), enabled: configRef.current.idleBubbleEnabled }),
      dismissBubble: () => dispatch({ type: 'dismiss', now: Date.now() }),
    };
  }, []);

  const snapshot = useMemo<MascotSnapshot>(() => ({ state: mascotCurrentState(runtime), reactionId: runtime.reactionId,
    bubble: runtime.bubble ? { id: runtime.bubble.id, text: runtime.bubble.text, priority: runtime.bubble.priority } : null,
    config, suppressed }), [runtime, config, suppressed]);

  return <MascotApiContext.Provider value={api}>
    <MascotSnapshotContext.Provider value={snapshot}>{children}</MascotSnapshotContext.Provider>
  </MascotApiContext.Provider>;
}
