import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test, { afterEach } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');
const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
const originalStorage = globalThis.localStorage;
let moduleId = 0;

afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.window = originalWindow;
  globalThis.localStorage = originalStorage;
});

function storage() {
  const values = new Map();
  return {
    values,
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
}

async function api(origin = 'https://api.example.com', base = '/lexi-read/') {
  globalThis.window = { location: { origin: 'https://reader.example.com' } };
  const environment = JSON.stringify({ VITE_API_ORIGIN: origin, BASE_URL: base });
  const js = ts.transpileModule(source.replaceAll('import.meta.env', `(${environment})`), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(`${js}\n// module ${++moduleId}`).toString('base64')}`);
}

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const health = mode => json({ status: 'ok', auth_mode: mode, ocr_provider: 'local', llm_provider: 'offline' });

test('concurrent public requests create one session and authorize JSON and image requests', async () => {
  globalThis.localStorage = storage();
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith('/health')) return health('guest');
    if (url.endsWith('/session')) return json({ token: 'guest-test-token', user_id: 20 });
    assert.equal(init.headers.get('Authorization'), 'Bearer guest-test-token');
    if (url.endsWith('/image')) return new Response(new Blob(['image'], { type: 'image/png' }));
    return json([]);
  };
  const client = await api();
  await Promise.all([client.request('/documents'), client.request('/vocabulary'), client.request('/events'), client.request('/health')]);
  const image = await client.requestImage('/api/documents/1/image');
  assert.equal(await image.text(), 'image');
  assert.equal(calls.filter(call => call.url.endsWith('/health')).length, 1);
  assert.equal(calls.filter(call => call.url.endsWith('/session')).length, 1);
  assert.equal(calls.find(call => call.url.endsWith('/session')).init.method, 'POST');
  assert.equal(calls[0].init.headers.has('Authorization'), false);
  assert.equal(globalThis.localStorage.getItem(client.browserStorageKey('guest-token')), 'guest-test-token');
  assert.ok(calls.every(call => !call.url.includes('guest-test-token')));

  // A new module simulates a page reload while preserving browser storage.
  const reloaded = await api();
  await reloaded.request('/documents');
  assert.equal(calls.filter(call => call.url.endsWith('/session')).length, 1);
});

test('an invalid saved credential is preserved and never replaced after 401', async () => {
  globalThis.localStorage = storage();
  const client = await api();
  globalThis.localStorage.setItem(client.browserStorageKey('guest-token'), 'expired-test-token');
  let sessions = 0;
  globalThis.fetch = async (url, init) => {
    if (url.endsWith('/health')) return health('guest');
    if (url.endsWith('/session')) { sessions += 1; return json({ token: 'replacement' }); }
    assert.equal(init.headers.get('Authorization'), 'Bearer expired-test-token');
    return json({ detail: 'Invalid credentials' }, 401);
  };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await assert.rejects(client.request('/documents'), error => error instanceof client.ApiError && error.status === 401 && error.message.includes('未自动新建空间'));
  }
  assert.equal(sessions, 0);
  assert.equal(globalThis.localStorage.getItem(client.browserStorageKey('guest-token')), 'expired-test-token');
});

test('local mode needs neither browser storage nor a guest session', async () => {
  globalThis.localStorage = { getItem() { throw new Error('storage blocked'); } };
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push(url);
    assert.equal(init.headers.has('Authorization'), false);
    if (url === '/api/health') return health('local');
    assert.notEqual(url, '/api/session');
    return json([]);
  };
  const client = await api('');
  await Promise.all([client.request('/documents'), client.request('/vocabulary')]);
  assert.equal(calls.filter(url => url === '/api/health').length, 1);
});

test('blocked browser storage stops public initialization before creating a guest', async () => {
  globalThis.localStorage = { getItem: () => null, setItem() { throw new Error('storage blocked'); } };
  const calls = [];
  globalThis.fetch = async url => { calls.push(url); return health('guest'); };
  const client = await api();
  await assert.rejects(client.request('/documents'), /浏览器存储/);
  assert.deepEqual(calls, ['https://api.example.com/api/health']);
});

test('transient health and session failures can be retried', async () => {
  globalThis.localStorage = storage();
  let healthCalls = 0;
  let sessionCalls = 0;
  globalThis.fetch = async url => {
    if (url.endsWith('/health')) { healthCalls += 1; if (healthCalls === 1) throw new TypeError('offline'); return health('guest'); }
    if (url.endsWith('/session')) { sessionCalls += 1; if (sessionCalls === 1) return json({ detail: 'Try later' }, 503); return json({ token: 'retried-token' }); }
    return json([]);
  };
  const client = await api();
  await assert.rejects(client.request('/documents'), /暂时无法连接服务/);
  await assert.rejects(client.request('/documents'), /Try later/);
  assert.deepEqual(await client.request('/documents'), []);
  assert.equal(healthCalls, 2);
  assert.equal(sessionCalls, 2);
});

test('image URLs cannot send saved credentials outside the configured backend API', async () => {
  globalThis.localStorage = storage();
  const client = await api();
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return json([]); };
  for (const url of ['https://other.example.com/api/image', '//other.example.com/api/image', 'https://user:pass@api.example.com/api/image', '/outside-api/image', 'data:image/png;base64,test']) {
    await assert.rejects(client.requestImage(url), /图片地址无效/);
  }
  assert.equal(calls, 0);
});

test('storage keys are isolated by backend origin and Pages project', async () => {
  const first = await api('https://api.example.com', '/first/');
  const otherBase = await api('https://api.example.com', '/second/');
  const otherBackend = await api('https://api2.example.com', '/first/');
  assert.notEqual(first.browserStorageKey('guest-token'), otherBase.browserStorageKey('guest-token'));
  assert.notEqual(first.browserStorageKey('guest-token'), otherBackend.browserStorageKey('guest-token'));
});

test('invalid API origins fail clearly without making any network requests', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return health('local'); };
  for (const origin of ['api.example.com', 'ftp://api.example.com', 'https://user:pass@api.example.com', 'https://@api.example.com', 'https://api.example.com/api', 'https://api.example.com/x/..', 'https://api.example.com?', 'https://api.example.com#', 'https://api.example.com\\path', 'https://api.example.com:invalid']) {
    const client = await api(origin);
    await assert.rejects(client.request('/health'), error => error instanceof client.ApiError && error.message.includes('VITE_API_ORIGIN'));
  }
  assert.equal(calls, 0);
});
