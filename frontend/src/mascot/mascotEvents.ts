type Operation = { operationId: string; word: string };
type Result = { source?: string; fallback?: boolean };

let nextOperationId = 0;

/** Local presentation identity; also works on HTTP LAN origins without Web Crypto. */
export function createMascotOperationId(prefix = 'operation'): string {
  nextOperationId += 1;
  return `${prefix}:${nextOperationId}`;
}

/** UI observations only: emitting these events never performs a learning action. */
export type MascotEvent =
  | { type: 'WORD_SELECTED'; word: string }
  | { type: 'WORD_MARKED'; word: string; status: 'KNOWN' | 'UNKNOWN' | 'UNCERTAIN' }
  | ({ type: 'ANALYSIS_STARTED' } & Operation)
  | ({ type: 'ANALYSIS_SUCCESS' } & Operation & Result)
  | ({ type: 'ANALYSIS_ERROR' } & Operation)
  | ({ type: 'RECOMMENDATION_STARTED' } & Operation)
  | ({ type: 'RECOMMENDATION_SUCCESS'; count: number } & Operation & Result)
  | ({ type: 'RECOMMENDATION_ERROR' } & Operation)
  | { type: 'FEEDBACK_SAVED'; requestId?: string; word?: string };
