import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test, { afterEach } from 'node:test';
import ts from 'typescript';

const [apiSource, streamSource, waitSource] = await Promise.all([
  readFile(new URL('../src/api.ts', import.meta.url), 'utf8'),
  readFile(new URL('../src/aiStream.ts', import.meta.url), 'utf8'),
  readFile(new URL('../src/aiWaitState.ts', import.meta.url), 'utf8'),
]);
const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
const originalStorage = globalThis.localStorage;
let moduleId = 0;
const moduleURL = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText).toString('base64')}`;
const wait = await import(moduleURL(waitSource));

afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.window = originalWindow;
  globalThis.localStorage = originalStorage;
});

async function client() {
  globalThis.window = { location: { origin: 'https://reader.example.com' } };
  const values = new Map();
  globalThis.localStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  const apiURL = moduleURL(`${apiSource.replaceAll('import.meta.env', '({VITE_API_ORIGIN:"https://api.example.com",BASE_URL:"/reader/"})')}\n// ${++moduleId}`);
  const api = await import(apiURL);
  const stream = await import(moduleURL(streamSource.replace("'./api'", JSON.stringify(apiURL))));
  return { ...api, ...stream };
}

const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const event = (name, value) => `event: ${name}\ndata: ${JSON.stringify(value)}\n\n`;
const result = { word: 'bank', detail: { summary: '银行；河岸' }, warning: null };

function response(text, chunkSize = 23) {
  const bytes = new TextEncoder().encode(text);
  let offset = 0;
  let cancellations = 0;
  const body = new ReadableStream({
    pull(controller) {
      if (offset >= bytes.length) { controller.close(); return; }
      controller.enqueue(bytes.slice(offset, offset + chunkSize));
      offset += chunkSize;
    },
    cancel() { cancellations += 1; },
  });
  return { response: new Response(body, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } }), cancellations: () => cancellations };
}

function backend(output, mode = 'local') {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith('/api/health')) return json({ auth_mode: mode });
    if (url.endsWith('/api/session')) return json({ token: 'guest-stream-test-token' });
    return typeof output === 'function' ? output(url, init) : output;
  };
  return calls;
}

test('SSE survives byte-split UTF-8, CRLF, comments and multiline data with the normal guest identity', async () => {
  const stream = response(': heartbeat\r\nevent: status\r\ndata: {"phase":\r\ndata: "generating"}\r\n\r\n'
    + 'event: future-event\r\ndata: not json\r\n\r\n'
    + event('preview', { text: '第一句：河岸。🙂\n第二句' }).replaceAll('\n', '\r\n')
    + event('status', { phase: 'validating' }) + event('result', result), 1);
  const calls = backend(stream.response, 'guest');
  const api = await client();
  const statuses = [], previews = [];
  const controller = new AbortController();
  assert.deepEqual(await api.requestAIStream('/dictionary/bank/explain-stream', { method: 'POST', signal: controller.signal }, {
    onStatus: phase => statuses.push(phase), onPreview: text => previews.push(text),
  }), result);
  assert.deepEqual(statuses, ['generating', 'validating']);
  assert.deepEqual(previews, ['第一句：河岸。🙂\n第二句']);
  const posts = calls.filter(call => call.url.endsWith('/explain-stream'));
  assert.equal(posts.length, 1);
  assert.equal(posts[0].init.method, 'POST');
  assert.equal(posts[0].init.body, undefined);
  assert.equal(posts[0].init.headers.get('Authorization'), 'Bearer guest-stream-test-token');
  assert.equal(posts[0].init.headers.get('Accept'), 'text/event-stream');
  assert.notEqual(posts[0].init.signal, controller.signal);
  assert.ok(calls.every(call => !call.url.includes('guest-stream-test-token')));
  assert.equal(stream.response.body.locked, false);
});

test('previews are full snapshots and a terminal result ignores every later event', async () => {
  const text = event('status', { phase: 'fallback' }) + event('preview', { text: '第一' })
    + event('preview', { text: '第一句话' }) + event('result', result)
    + event('preview', { text: '不应显示' }) + event('status', { phase: 'generating' })
    + event('result', { word: 'other' });
  const stream = response(text, 65_536);
  backend(stream.response);
  const api = await client();
  const previews = [], statuses = [];
  assert.deepEqual(await api.requestAIStream('/generate', { method: 'POST' }, {
    onPreview: value => previews.push(value), onStatus: value => statuses.push(value),
  }), result);
  assert.deepEqual(previews, ['第一', '第一句话']);
  assert.deepEqual(statuses, ['fallback']);
  assert.equal(stream.response.body.locked, false);
});

test('one JSON compatibility response is complete without a second request or synthetic previews', async () => {
  const calls = backend(json(result));
  const api = await client();
  let updates = 0;
  assert.deepEqual(await api.requestAIStream('/generate', { method: 'POST' }, {
    onStatus: () => updates++, onPreview: () => updates++,
  }), result);
  assert.equal(updates, 0);
  assert.equal(calls.filter(call => call.url.endsWith('/generate')).length, 1);
});

test('draft-only, malformed and truncated responses never count as successful completion', async () => {
  for (const text of ['', event('preview', { text: '草稿' }), event('status', { phase: 'generating' }),
    'event: result\ndata: {"word":"bank"}', 'event: result\ndata: {broken}\n\n',
    event('preview', { text: 12 }), event('status', { phase: 'finished' })]) {
    const stream = response(text);
    const calls = backend(stream.response);
    const api = await client();
    await assert.rejects(api.requestAIStream('/generate', { method: 'POST' }), error => error instanceof api.ApiError && /不完整|未收到完整结果/.test(error.message));
    assert.equal(calls.filter(call => call.url.endsWith('/generate')).length, 1);
    assert.equal(stream.response.body.locked, false);
  }
});

test('server error events use safe messages and status, and never retry POST', async () => {
  for (const [message, expected] of [['模型暂不可用，请重试。', '模型暂不可用，请重试。'],
    ['Authorization: Bearer private-token', '生成暂时失败，请稍后重试。'],
    ['<script>private</script>', '生成暂时失败，请稍后重试。'],
    ['https://provider.example.com?key=private', '生成暂时失败，请稍后重试。']]) {
    const calls = backend(response(event('preview', { text: '尚未完成' }) + event('error', { message, status: 503 }) + event('result', result)).response);
    const api = await client();
    await assert.rejects(api.requestAIStream('/generate', { method: 'POST' }), error => error instanceof api.ApiError && error.status === 503 && error.message === expected);
    assert.equal(calls.filter(call => call.url.endsWith('/generate')).length, 1);
  }
});

test('HTTP failures preserve ApiError and never fall back to another endpoint', async () => {
  const calls = backend(json({ detail: 'AI 暂不可用，请稍后重试。' }, 503));
  const api = await client();
  await assert.rejects(api.requestAIStream('/generate', { method: 'POST' }), error => error instanceof api.ApiError && error.status === 503);
  assert.equal(calls.filter(call => call.url.endsWith('/generate')).length, 1);
});

test('abort cancels and releases the reader while blocking subsequent preview callbacks', async () => {
  let cancellations = 0;
  const body = new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode(event('status', { phase: 'generating' }) + event('preview', { text: '过期草稿' }))); },
    cancel() { cancellations += 1; throw new Error('already closed'); },
  });
  backend(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
  const api = await client();
  const controller = new AbortController();
  let previews = 0;
  await assert.rejects(api.requestAIStream('/generate', { method: 'POST', signal: controller.signal }, {
    onStatus: () => controller.abort(), onPreview: () => previews++,
  }), error => error.name === 'AbortError');
  assert.equal(cancellations, 1);
  assert.equal(previews, 0);
  assert.equal(body.locked, false);
});

test('an already aborted signal makes no backend request', async () => {
  const calls = backend(json(result));
  const api = await client();
  const controller = new AbortController(); controller.abort();
  await assert.rejects(api.requestAIStream('/generate', { method: 'POST', signal: controller.signal }), error => error.name === 'AbortError');
  assert.deepEqual(calls, []);
});

test('bounded SSE, JSON and unknown event buffers reject oversized data without a retry', async () => {
  for (const output of [response(event('result', { text: 'x'.repeat(128 * 1_024) })).response,
    response(`event: future-event\ndata: ${'x'.repeat(128 * 1_024)}\n\n`).response,
    response(`:${'x'.repeat(128 * 1_024)}\n\n`).response, json({ text: 'x'.repeat(128 * 1_024) })]) {
    const calls = backend(output);
    const api = await client();
    await assert.rejects(api.requestAIStream('/generate', { method: 'POST' }), error => error instanceof api.ApiError);
    assert.equal(calls.filter(call => call.url.endsWith('/generate')).length, 1);
    assert.equal(output.body.locked, false);
  }
  const full = { text: 'x'.repeat(120 * 1_024) };
  backend(response(event('result', full), 65_536).response);
  const api = await client();
  assert.deepEqual(await api.requestAIStream('/generate', { method: 'POST' }), full);
});

test('network interruption and invalid UTF-8 reject safely without leaking transport messages', async () => {
  for (const body of [new ReadableStream({ start(controller) { controller.error(new TypeError('private provider transport details')); } }),
    new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array([0xc3, 0x28])); controller.close(); } })]) {
    backend(new Response(body, { headers: { 'Content-Type': 'text/event-stream' } }));
    const api = await client();
    await assert.rejects(api.requestAIStream('/generate', { method: 'POST' }), error => error instanceof api.ApiError && !error.message.includes('private'));
    assert.equal(body.locked, false);
  }
});

test('waiting labels use the reported phase and never infer a generation stage from elapsed time', () => {
  assert.equal(wait.aiWaitPhaseLabel(), '正在等待模型响应');
  assert.equal(wait.aiWaitPhaseLabel('connecting'), '正在连接 AI 服务');
  assert.equal(wait.aiWaitPhaseLabel('generating'), '正在生成内容');
  assert.equal(wait.aiWaitPhaseLabel('validating'), '正在核对内容');
  assert.equal(wait.aiWaitPhaseLabel('fallback'), '正在等待完整内容');
  assert.equal(wait.aiWaitIsSlow(0), false);
  assert.equal(wait.aiWaitIsSlow(14), false);
  assert.equal(wait.aiWaitIsSlow(15), true);
  assert.equal(wait.aiWaitIsSlow(180), true);
  assert.equal(wait.aiWaitPhaseLabel('connecting'), '正在连接 AI 服务');
});

test('elapsed time counts whole seconds from this mounted request and tolerates clock adjustment', () => {
  assert.equal(wait.aiWaitElapsedSeconds(10_000, 10_000), 0);
  assert.equal(wait.aiWaitElapsedSeconds(10_000, 10_999), 0);
  assert.equal(wait.aiWaitElapsedSeconds(10_000, 11_000), 1);
  assert.equal(wait.aiWaitElapsedSeconds(10_000, 25_700), 15);
  assert.equal(wait.aiWaitElapsedSeconds(10_000, 9_000), 0);
  assert.equal(wait.aiWaitElapsedSeconds(25_700, 25_700), 0);
});
