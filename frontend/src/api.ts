import type { Health } from './types';

export class ApiError extends Error {
  constructor(message: string, public status = 0) { super(message); }
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
export async function reviewStorageKey(): Promise<string> {
  const mode = (await getHealth()).auth_mode;
  const identity = mode === 'guest' ? `guest:${await guestToken()}` : 'local';
  return browserStorageKey(`review-session:v1:${identity}`);
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
        : `请求失败（${response.status}），请稍后重试。`;
      throw new ApiError(detail, response.status);
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
  const url = new URL(backendUrl(path), window.location.origin);
  const origin = apiOrigin() || window.location.origin;
  if (url.origin !== origin || !url.pathname.startsWith('/api/') || url.username || url.password) throw new ApiError('图片地址无效，请重新打开阅读材料。');
  const response = await fetchBackend(`${url.pathname}${url.search}`, { signal });
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
