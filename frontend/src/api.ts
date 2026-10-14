export class ApiError extends Error {
  constructor(message: string, public status = 0) { super(message); }
}

/** Same-origin requests go through Vite in development and FastAPI in a built deployment. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const timeout = AbortSignal.timeout(180_000);
  try {
    const response = await fetch(`/api${path}`, {
      ...init,
      headers: init.body instanceof FormData ? init.headers : { 'Content-Type': 'application/json', ...init.headers },
      signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      const detail = typeof data?.detail === 'string' ? data.detail
        : Array.isArray(data?.detail) ? data.detail.map((item: { msg: string }) => item.msg).join('；')
        : `请求失败（${response.status}），请稍后重试。`;
      throw new ApiError(detail, response.status);
    }
    return await response.json() as T;
  } catch (error) {
    if (error instanceof ApiError || (error instanceof Error && error.name === 'AbortError')) throw error;
    if (error instanceof Error && error.name === 'TimeoutError') throw new ApiError('处理超时。OCR 首次运行可能需要下载模型，请稍后重试。');
    throw new ApiError('暂时无法连接服务，请确认后端已启动后重试。');
  }
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
