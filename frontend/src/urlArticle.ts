export const URL_IMPORT_LIMIT = 2048;
export interface UrlImportPayload { url: string; request_id: string }

/** Validate URL syntax here; the server checks network destinations before fetching. */
export function urlImportProblem(value: string): string {
  const url = value.trim();
  if (!url) return '请先粘贴英文文章的网页链接。';
  if (Array.from(url).length > URL_IMPORT_LIMIT) return '网页链接最多支持 2,048 字，请缩短链接后重试。';
  if (/[\u0000-\u0020\u007f\\]/.test(url)) return '网页链接不能包含空格、控制字符或反斜线。';
  if (!/^https?:\/\//i.test(url)) return '请使用以 http:// 或 https:// 开头的完整网页链接。';
  try {
    const parsed = new URL(url);
    if (!parsed.hostname) return '请填写有效的网页链接。';
    if (parsed.username || parsed.password) return '请使用不含账号或密码的公开网页链接。';
    if (parsed.port) return '请使用默认端口的公开网页链接。';
  } catch { return '请填写有效的网页链接。'; }
  return '';
}

/** Imported metadata is untrusted: never turn arbitrary schemes into clickable links. */
export function originalWebLink(value: string | null | undefined): { href: string; host: string } | null {
  if (!value || urlImportProblem(value)) return null;
  const parsed = new URL(value.trim());
  return { href: parsed.href, host: parsed.hostname };
}
