import { ApiError, requestStreamResponse } from './api';

export type AIStreamPhase = 'connecting' | 'generating' | 'validating' | 'fallback';
export interface AIStreamHandlers {
  onStatus?: (phase: AIStreamPhase) => void;
  /** A complete, provisional snapshot; callers must replace their previous draft. */
  onPreview?: (text: string) => void;
}

const MAX_EVENT_LENGTH = 128 * 1_024;
const INCOMPLETE_MESSAGE = '生成连接已中断，尚未收到完整结果，请重试。';
const INVALID_MESSAGE = '生成服务返回的内容不完整，请重试。';
const ERROR_MESSAGE = '生成暂时失败，请稍后重试。';
const phases: ReadonlySet<string> = new Set(['connecting', 'generating', 'validating', 'fallback']);

function streamError(value: unknown): ApiError {
  const data = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  const raw = typeof data.message === 'string' ? data.message.trim() : '';
  // Never surface provider credentials, stack traces, markup or arbitrary endpoints.
  const safe = raw && raw.length <= 300 && !/[\p{Cc}\p{Cs}<>]/u.test(raw)
    && !/(?:bearer\s|authorization|api[_ -]?key|sk-[a-z0-9]|traceback|stack\s?trace|https?:\/\/)/i.test(raw);
  const status = typeof data.status === 'number' && Number.isInteger(data.status)
    && data.status >= 100 && data.status <= 599 ? data.status : 0;
  return new ApiError(safe ? raw : ERROR_MESSAGE, status);
}

function aborted(signal: AbortSignal): Error {
  return signal.reason instanceof Error ? signal.reason : new DOMException('已停止等待。', 'AbortError');
}

/** Read one SSE response. A draft is never a successful result, and POSTs are never retried. */
export async function requestAIStream<T>(path: string, init: RequestInit, handlers: AIStreamHandlers = {}): Promise<T> {
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let cancelRequested = false;
  const cancel = () => {
    if (cancelRequested || !reader) return;
    cancelRequested = true;
    // A stopped or failed stream may reject cancellation after closing its socket.
    void reader.cancel().catch(() => undefined);
  };
  try {
    if (init.signal?.aborted) throw aborted(init.signal);
    const response = await requestStreamResponse(path, init);
    if (init.signal?.aborted) {
      void response.body?.cancel().catch(() => undefined);
      throw aborted(init.signal);
    }
    if (!response.body) throw new ApiError(INCOMPLETE_MESSAGE);
    reader = response.body.getReader();
    init.signal?.addEventListener('abort', cancel, { once: true });
    const contentType = response.headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase();
    const jsonResponse = contentType === 'application/json' || Boolean(contentType?.endsWith('+json'));
    if (!jsonResponse && contentType !== 'text/event-stream') throw new ApiError(INVALID_MESSAGE);

    const decoder = new TextDecoder('utf-8', { fatal: true });
    let json = '';
    let line = '';
    let previousCR = false;
    let eventName = '';
    let dataLines: string[] = [];
    let eventLength = 0;
    let terminal = false;
    let result: T | undefined;
    const dispatch = () => {
      const event = eventName;
      const data = dataLines.join('\n');
      eventName = ''; dataLines = []; eventLength = 0;
      if (!data || !['status', 'preview', 'result', 'error'].includes(event)) return;
      let value: unknown;
      try { value = JSON.parse(data); } catch { throw new ApiError(INVALID_MESSAGE); }
      if (event === 'error') throw streamError(value);
      if (event === 'result') { result = value as T; terminal = true; return; }
      const record = value && typeof value === 'object' ? value as Record<string, unknown> : {};
      if (event === 'status') {
        if (typeof record.phase !== 'string' || !phases.has(record.phase)) throw new ApiError(INVALID_MESSAGE);
        handlers.onStatus?.(record.phase as AIStreamPhase);
      } else {
        if (typeof record.text !== 'string') throw new ApiError(INVALID_MESSAGE);
        handlers.onPreview?.(record.text);
      }
    };
    const finishLine = () => {
      const current = line; line = '';
      if (!current) { dispatch(); return; }
      if (current.startsWith(':')) return;
      const colon = current.indexOf(':');
      const name = colon < 0 ? current : current.slice(0, colon);
      let value = colon < 0 ? '' : current.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (name === 'event') eventName = value;
      else if (name === 'data') {
        eventLength += value.length + 1;
        if (eventLength > MAX_EVENT_LENGTH) throw new ApiError(INVALID_MESSAGE);
        dataLines.push(value);
      }
    };
    const consume = (text: string) => {
      if (jsonResponse) {
        if (json.length + text.length > MAX_EVENT_LENGTH) throw new ApiError(INVALID_MESSAGE);
        json += text;
        return;
      }
      for (const character of text) {
        if (init.signal?.aborted) throw aborted(init.signal);
        if (previousCR) { previousCR = false; if (character === '\n') continue; }
        if (character === '\r' || character === '\n') {
          previousCR = character === '\r';
          finishLine();
          if (terminal) return;
        } else {
          line += character;
          if (line.length > MAX_EVENT_LENGTH) throw new ApiError(INVALID_MESSAGE);
        }
      }
    };
    while (!terminal) {
      const { value, done } = await reader.read();
      if (init.signal?.aborted) throw aborted(init.signal);
      if (done) {
        consume(decoder.decode());
        if (jsonResponse) {
          try { result = JSON.parse(json) as T; terminal = true; }
          catch { throw new ApiError(INVALID_MESSAGE); }
        }
        // SSE events must end with a blank line, including the terminal result.
        if (!terminal) throw new ApiError(INCOMPLETE_MESSAGE);
        break;
      }
      // Limit temporary decoding allocations even if a transport supplies a huge chunk.
      for (let offset = 0; offset < value.length && !terminal; offset += 16_384) {
        consume(decoder.decode(value.subarray(offset, offset + 16_384), { stream: true }));
      }
    }
    return result as T;
  } catch (error) {
    if (init.signal?.aborted) throw aborted(init.signal);
    if (error instanceof ApiError || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (error instanceof Error && error.name === 'TimeoutError') throw new ApiError('生成超时，请稍后重试。');
    throw new ApiError(INCOMPLETE_MESSAGE);
  } finally {
    init.signal?.removeEventListener('abort', cancel);
    cancel();
    reader?.releaseLock();
  }
}
