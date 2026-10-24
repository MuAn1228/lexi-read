import type { Health } from './types';
import type { PronunciationAudio, PronunciationAttribution, SpeechAccent } from './speech';

export class ApiError extends Error {
  constructor(message: string, public status = 0, public code = '') { super(message); }
}

function apiOrigin(): string {
  const configured = import.meta.env.VITE_API_ORIGIN?.trim() ?? '';
  if (!configured) return '';
  try {
    // An origin may have a trailing slash, but no credentials, path, query or fragment.
    if (!/^https?:\/\/[^/?#\\\s@]+\/?$/i.test(configured)) throw new Error();
    const url = new URL(configured);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/') throw new Error();
    return url.origin;
  } catch {
    // Validate on use so configuration errors reach the existing request error UI.
    throw new ApiError('VITE_API_ORIGIN 配置无效：请填写 HTTP(S) 服务源地址（例如 https://api.example.com），不要包含路径、账号、查询参数或片段。');
  }
}

/** Resolve backend paths while preserving absolute image URLs and the same-origin default. */
export function backendUrl(path: string): string {
  return path.startsWith('/') && !path.startsWith('//') ? `${apiOrigin()}${path}` : path;
}

/** Isolate browser state between backends and different Pages projects. */
export function browserStorageKey(name: string): string {
  return `lexi:${apiOrigin() || window.location.origin}:${import.meta.env.BASE_URL}:${name}`;
}

let healthPromise: Promise<Health> | null = null;
let sessionPromise: Promise<string> | null = null;

function getHealth(): Promise<Health> {
  if (!healthPromise) {
    healthPromise = fetchBackend('/api/health', {}, false)
      .then(response => response.json() as Promise<Health>)
      .then(health => {
        if (health.auth_mode !== 'guest' && health.auth_mode !== 'local') throw new ApiError('服务版本不兼容，请更新后端后重新连接。');
        return health;
      })
      .catch(error => { healthPromise = null; throw error; });
  }
  return healthPromise;
}

function guestToken(): Promise<string> {
  if (!sessionPromise) {
    sessionPromise = (async () => {
      const key = browserStorageKey('guest-token');
      let saved: string | null;
      try {
        saved = localStorage.getItem(key);
        // Check persistence before creating an identity with no way to return to it.
        if (!saved) { localStorage.setItem(`${key}:check`, '1'); localStorage.removeItem(`${key}:check`); }
      } catch { throw new ApiError('无法保存学习空间凭证。请允许此站点使用浏览器存储后重试。'); }
      if (saved) return saved;
      const response = await fetchBackend('/api/session', { method: 'POST' }, false);
      const session = await response.json() as { token?: string };
      if (!session.token || typeof session.token !== 'string') throw new ApiError('学习空间创建失败，请稍后重试。');
      try { localStorage.setItem(key, session.token); }
      catch { throw new ApiError('无法保存学习空间凭证。请允许此站点使用浏览器存储后重试。'); }
      return session.token;
    })().catch(error => { sessionPromise = null; throw error; });
  }
  return sessionPromise;
}

/** Internal persistence scope; never render this value or put it in a URL. */
export async function learningStorageKey(name: string): Promise<string> {
  const mode = (await getHealth()).auth_mode;
  const identity = mode === 'guest' ? `guest:${await guestToken()}` : 'local';
  return browserStorageKey(`${name}:${identity}`);
}

export async function reviewStorageKey(): Promise<string> {
  return learningStorageKey('review-session:v1');
}

async function fetchBackend(path: string, init: RequestInit = {}, authenticated = true): Promise<Response> {
  const timeout = AbortSignal.timeout(180_000);
  try {
    const headers = new Headers(init.headers);
    if (!(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const guest = authenticated && (await getHealth()).auth_mode === 'guest';
    if (guest) headers.set('Authorization', `Bearer ${await guestToken()}`);
    const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
    signal.throwIfAborted();
    const response = await fetch(backendUrl(path), {
      ...init,
      headers,
      signal,
    });
    if (!response.ok) {
      if (response.status === 401 && guest) throw new ApiError('学习空间凭证已失效。为保护已有记录，未自动新建空间；请联系站点管理员处理。', 401);
      const data = await response.json().catch(() => null);
      const detail = typeof data?.detail === 'string' ? data.detail
        : Array.isArray(data?.detail) ? data.detail.map((item: { msg: string }) => item.msg).join('；')
        : typeof data?.detail?.message === 'string' ? data.detail.message
        : `请求失败（${response.status}），请稍后重试。`;
      throw new ApiError(detail, response.status, typeof data?.detail?.code === 'string' ? data.detail.code : '');
    }
    return response;
  } catch (error) {
    if (error instanceof ApiError || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (error instanceof Error && error.name === 'TimeoutError') throw new ApiError('处理超时。OCR 首次运行可能需要下载模型，请稍后重试。');
    throw new ApiError('暂时无法连接服务，请确认后端已启动后重试。');
  }
}

/** Empty VITE_API_ORIGIN uses Vite's development proxy or FastAPI's built deployment. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (path === '/health') return await getHealth() as T;
  const response = await fetchBackend(`/api${path}`, init);
  return await response.json() as T;
}

/** Never send a saved credential to an image host supplied by document data. */
export async function requestImage(path: string, signal?: AbortSignal): Promise<Blob> {
  return await requestBlob(path, { signal }, '图片地址无效，请重新打开阅读材料。');
}

/** Fetch downloads with the same identity as JSON requests, never a token in the URL. */
export async function requestFile(path: string, init: RequestInit = {}): Promise<Blob> {
  return await requestBlob(path, init, '文件地址无效，请重新打开阅读材料。');
}

function pronunciationAttribution(raw: string | null): PronunciationAttribution | null {
  if (!raw || raw.length > 4_096) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const data = value as Record<string, unknown>;
    const plain = (text: unknown, limit: number): text is string => typeof text === 'string'
      && Boolean(text.trim()) && text.length <= limit && !/[\p{Cc}\p{Cs}<>]/u.test(text);
    if (!plain(data.author, 240) || !plain(data.license, 120)) return null;
    const allowedURL = (text: unknown, source: boolean): text is string => {
      if (typeof text !== 'string' || text.length > 2_048 || /[\s\\<>]/u.test(text)) return false;
      const url = new URL(text);
      if (url.protocol !== 'https:' || url.username || url.password || url.port || url.search || url.hash) return false;
      return source ? url.hostname === 'commons.wikimedia.org' && /^\/wiki\/File:.+/.test(url.pathname)
        : url.hostname === 'creativecommons.org' && /^\/(?:licenses|publicdomain)\/.+/.test(url.pathname);
    };
    if (!allowedURL(data.source_url, true) || !allowedURL(data.license_url, false)) return null;
    return { author: data.author.trim(), license: data.license.trim(), source_url: data.source_url, license_url: data.license_url };
  } catch { return null; }
}

/** Audio and verified recording credits share the same authenticated backend response. */
export async function requestPronunciation(text: string, accent: SpeechAccent, signal?: AbortSignal, wordFallback = false): Promise<PronunciationAudio> {
  const response = await fetchBackend('/api/pronunciation', { method: 'POST', body: JSON.stringify({ text, accent, allow_word_synthesis: wordFallback }), signal });
  const source = response.headers.get('X-Pronunciation-Source');
  if (source !== 'dictionary' && source !== 'piper') throw new ApiError('发音来源信息不完整，请重试。');
  const returnedAccent = response.headers.get('X-Pronunciation-Accent');
  if (returnedAccent !== null && returnedAccent !== 'en-US' && returnedAccent !== 'en-GB') throw new ApiError('发音口音信息不完整，请重试。');
  const attribution = source === 'dictionary' ? pronunciationAttribution(response.headers.get('X-Pronunciation-Attribution')) : null;
  if (source === 'dictionary' && !attribution) throw new ApiError('词典录音的来源信息不完整，请重试。');
  const blob = await response.blob();
  if (!blob.size) throw new ApiError('发音音频为空，请重试。');
  return { blob, source, accent: returnedAccent ?? accent, ...(attribution ? { attribution } : {}) };
}

async function requestBlob(path: string, init: RequestInit, invalidMessage: string): Promise<Blob> {
  const url = new URL(backendUrl(path), window.location.origin);
  const origin = apiOrigin() || window.location.origin;
  if (url.origin !== origin || !url.pathname.startsWith('/api/') || url.username || url.password) throw new ApiError(invalidMessage);
  const response = await fetchBackend(`${url.pathname}${url.search}`, init);
  return await response.blob();
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '发生了意外错误，请重试。';
}

export function formatDate(value: string | null): string {
  if (!value) return '尚未在原文遇见';
  // Server timestamps without offsets are UTC; explicitly apply that rather than browser-local time.
  const normalized = /[zZ]$|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(normalized));
}
