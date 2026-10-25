import { createContext, useContext } from 'react';
import type { MascotApi, MascotSnapshot } from './mascotTypes';

export const MascotApiContext = createContext<MascotApi | null>(null);
export const MascotSnapshotContext = createContext<MascotSnapshot | null>(null);

/** Stable commands: safe to use in existing effect dependency arrays. */
export function useMascot(): MascotApi {
  const value = useContext(MascotApiContext);
  if (!value) throw new Error('useMascot requires MascotProvider');
  return value;
}

/** Only the visual widget subscribes to animation and bubble changes. */
export function useMascotSnapshot(): MascotSnapshot {
  const value = useContext(MascotSnapshotContext);
  if (!value) throw new Error('useMascotSnapshot requires MascotProvider');
  return value;
}
