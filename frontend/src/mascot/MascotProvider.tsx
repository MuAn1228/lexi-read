import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { MascotApiContext, MascotSnapshotContext } from './useMascot';
import { createMascotRuntime, defaultMascotConfig, mascotCurrentState, mascotInteractionBlocked, mascotNextExpiry, MASCOT_STORAGE_KEY, normalizeMascotConfig, parseMascotConfig, reduceMascot } from './mascotState';
import type { MascotAction } from './mascotState';
import { createMascotInteraction, reduceMascotInteraction } from './mascotInteraction';
import type { MascotInteractionAction } from './mascotInteraction';
import type { MascotApi, MascotConfig, MascotSnapshot } from './mascotTypes';

function readConfig(): MascotConfig {
  try { return parseMascotConfig(window.localStorage.getItem(MASCOT_STORAGE_KEY)); }
  catch { return { ...defaultMascotConfig }; }
}

export function MascotProvider({ children }: { children: ReactNode }) {
  const [runtime, setRuntime] = useState(createMascotRuntime);
  const runtimeRef = useRef(runtime);
  const [interaction, setInteraction] = useState(createMascotInteraction);
  const interactionRef = useRef(interaction);
  const [config, setConfig] = useState(readConfig);
  const configRef = useRef(config);
  const [suppressed, setSuppressed] = useState(false);
  const suppressedRef = useRef(suppressed);

  // Update refs before React commits so rapid clicks and a START followed by a
  // click in the same event always see the latest state, without changing API identity.
  const sendRuntime = useCallback((action: MascotAction) => {
    const next = reduceMascot(runtimeRef.current, action);
    runtimeRef.current = next;
    setRuntime(next);
    return next;
  }, []);
  const sendInteraction = useCallback((action: MascotInteractionAction) => {
    const next = reduceMascotInteraction(interactionRef.current, action);
    interactionRef.current = next;
    setInteraction(next);
    return next;
  }, []);

  useEffect(() => {
    try { window.localStorage.setItem(MASCOT_STORAGE_KEY, JSON.stringify(config)); }
    catch { /* Private browsing or full storage must never interrupt reading. */ }
  }, [config]);

  const expires = mascotNextExpiry(runtime);
  useEffect(() => {
    if (expires === null) return;
    const timeout = window.setTimeout(() => sendRuntime({ type: 'tick', now: Date.now() }), Math.max(0, expires - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [expires, sendRuntime]);

  const gestureExpires = interaction.gesture?.until;
  useEffect(() => {
    if (gestureExpires === undefined) return;
    const timeout = window.setTimeout(() => sendInteraction({ type: 'tick', now: Date.now() }), Math.max(0, gestureExpires - Date.now()));
    return () => window.clearTimeout(timeout);
  }, [gestureExpires, sendInteraction]);

  const interactionBlocked = mascotInteractionBlocked(runtime, Date.now());
  const interactionVisible = !config.hidden && !suppressed && config.interactionEnabled && !interactionBlocked;
  useEffect(() => {
    if (!interactionVisible) sendInteraction({ type: 'cancel' });
  }, [interactionVisible, sendInteraction]);

  const api = useMemo<MascotApi>(() => {
    const updateConfig = (patch: Partial<MascotConfig>) => {
      const next = normalizeMascotConfig({ ...configRef.current, ...patch });
      configRef.current = next;
      setConfig(next);
      if (next.hidden || !next.interactionEnabled) sendInteraction({ type: 'cancel' });
    };
    const canInteract = (now: number) => !configRef.current.hidden && !suppressedRef.current
      && configRef.current.interactionEnabled && !mascotInteractionBlocked(runtimeRef.current, now);
    return {
      emit: event => {
        // An optional presentation layer must never throw into an existing API flow.
        try {
          const now = Date.now();
          const next = sendRuntime({ type: 'event', event, now, sample: Math.random(), choice: Math.random() });
          if (mascotInteractionBlocked(next, now)) sendInteraction({ type: 'cancel' });
        }
        catch { /* Keep the confirmed business action intact. */ }
      },
      cancelOperation: operationId => sendRuntime({ type: 'cancel', operationId, now: Date.now() }),
      setEnvironment: value => {
        suppressedRef.current = value.suppressed;
        setSuppressed(value.suppressed);
        if (value.suppressed) sendInteraction({ type: 'cancel' });
      },
      updateConfig,
      resetPosition: () => updateConfig({ x: null, y: null, anchor: 'right', side: 'right' }),
      show: () => updateConfig({ hidden: false }),
      hide: () => updateConfig({ hidden: true }),
      interact: () => {
        const now = Date.now();
        sendRuntime({ type: 'interact', now, choice: Math.random(), enabled: configRef.current.idleBubbleEnabled });
        sendInteraction({ type: 'click', now, choice: Math.random(), enabled: canInteract(now), blocked: mascotInteractionBlocked(runtimeRef.current, now) });
      },
      dismissBubble: () => sendRuntime({ type: 'dismiss', now: Date.now() }),
      playGesture: kind => {
        const now = Date.now();
        const previousId = interactionRef.current.gesture?.id;
        const next = sendInteraction({ type: 'play', kind, now, enabled: canInteract(now), blocked: mascotInteractionBlocked(runtimeRef.current, now) });
        if (next.gesture && next.gesture.id !== previousId) {
          sendRuntime({ type: 'gestureLine', kind, now, choice: Math.random(), enabled: configRef.current.idleBubbleEnabled });
        }
      },
      cancelGesture: () => { sendInteraction({ type: 'cancel' }); },
    };
  }, []);

  const snapshot = useMemo<MascotSnapshot>(() => ({ state: mascotCurrentState(runtime), reactionId: runtime.reactionId,
    bubble: runtime.bubble ? { id: runtime.bubble.id, text: runtime.bubble.text, priority: runtime.bubble.priority } : null,
    config, suppressed, gesture: interactionVisible ? interaction.gesture : null, interactionBlocked }),
  [runtime, config, suppressed, interaction, interactionVisible, interactionBlocked]);

  return <MascotApiContext.Provider value={api}>
    <MascotSnapshotContext.Provider value={snapshot}>{children}</MascotSnapshotContext.Provider>
  </MascotApiContext.Provider>;
}
