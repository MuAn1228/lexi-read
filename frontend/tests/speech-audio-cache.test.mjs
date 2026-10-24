import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/speechAudioCache.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { createPronunciationCache } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const credits = { author: 'Example recorder', license: 'CC BY-SA 4.0',
  source_url: 'https://commons.wikimedia.org/wiki/File:Example.ogg', license_url: 'https://creativecommons.org/licenses/by-sa/4.0/' };
const audio = (source = 'dictionary', size = 16) => ({ source, blob: new Blob(['a'.repeat(size)]), ...(source === 'dictionary' ? { attribution: { ...credits } } : {}) });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let i = 0; i < 10; ++i) await Promise.resolve(); };
const signal = () => new AbortController().signal;

function resource({ load, scope, ...options } = {}) {
  const calls = [];
  const cache = createPronunciationCache({ ...options, getScope: scope ?? (() => Promise.resolve('local-page')),
    load: (text, accent, requestSignal) => { calls.push({ text, accent, signal: requestSignal }); return load ? load(calls.at(-1)) : Promise.resolve(audio()); } });
  const get = (word = 'example', accent = 'en-US', requestSignal = signal()) => cache.load(word, accent, requestSignal);
  return { cache, calls, get };
}

test('repeat playback reuses a successful Blob and complete recording credits without retaining caller mutations', async () => {
  const { calls, get } = resource();
  const first = await get(' example ');
  first.attribution.author = 'Changed by caller';
  const second = await get('example');
  assert.equal(calls.length, 1);
  assert.equal(second.blob, first.blob);
  assert.deepEqual(second.attribution, credits);
});

test('text and selected accent remain separate cache keys', async () => {
  const { calls, get } = resource();
  await get('read'); await get('read', 'en-GB'); await get('reader'); await get('Read');
  await get('read'); await get('read', 'en-GB');
  assert.equal(calls.length, 4);
});

test('preparation and a click share one request while cancellation only releases its own consumer', async () => {
  const response = deferred(); const prepare = new AbortController(), playback = new AbortController();
  const { calls, get } = resource({ load: () => response.promise });
  const waiting = get('example', 'en-US', prepare.signal);
  const clicked = get('example', 'en-US', playback.signal);
  await flush();
  prepare.abort();
  await assert.rejects(waiting, error => error.name === 'AbortError');
  assert.equal(calls.length, 1); assert.equal(calls[0].signal.aborted, false);
  response.resolve(audio()); await clicked;
  await get(); assert.equal(calls.length, 1);
});

test('abandoning all consumers aborts fetching and a late success cannot become a cached result', async () => {
  const response = deferred(), request = new AbortController();
  let count = 0;
  const { calls, get } = resource({ load: () => ++count === 1 ? response.promise : Promise.resolve(audio()) });
  const waiting = get('example', 'en-US', request.signal); await flush();
  request.abort(); await assert.rejects(waiting, error => error.name === 'AbortError');
  assert.equal(calls[0].signal.aborted, true);
  response.resolve(audio()); await flush();
  await get(); assert.equal(calls.length, 2);
});

test('a canceled consumer cannot return a warm cache hit or start a new request', async () => {
  const { calls, get } = resource(); await get();
  const abort = new AbortController(); abort.abort();
  await assert.rejects(get('example', 'en-US', abort.signal), error => error.name === 'AbortError');
  await assert.rejects(get('different', 'en-US', abort.signal), error => error.name === 'AbortError');
  assert.equal(calls.length, 1);
});

test('failed, empty and uncredited responses are retried instead of cached', async () => {
  let count = 0;
  const { calls, get } = resource({ load: () => {
    ++count;
    if (count === 1) return Promise.reject(new Error('offline'));
    if (count === 2) return Promise.resolve(audio('dictionary', 0));
    if (count === 3) return Promise.resolve({ blob: new Blob(['a']), source: 'dictionary' });
    return Promise.resolve(audio());
  } });
  for (let i = 0; i < 3; ++i) await assert.rejects(get());
  await get(); await get(); assert.equal(calls.length, 4);
});

test('dictionary recordings live for thirty minutes while fallback synthesis retries the source after fifteen seconds', async () => {
  let now = 1_000;
  const dictionary = resource({ now: () => now });
  const fallback = resource({ now: () => now, load: () => Promise.resolve(audio('piper')) });
  await dictionary.get(); await fallback.get();
  now += 14_999; await dictionary.get(); await fallback.get();
  assert.equal(dictionary.calls.length, 1); assert.equal(fallback.calls.length, 1);
  ++now; await fallback.get(); assert.equal(fallback.calls.length, 2);
  now = 1_000 + 30 * 60_000; await dictionary.get(); assert.equal(dictionary.calls.length, 2);
});

test('entry capacity uses least-recently-used eviction', async () => {
  const { calls, get } = resource({ maxEntries: 2 });
  await get('first'); await get('second'); await get('first'); await get('third');
  await get('first'); assert.equal(calls.length, 3);
  await get('second'); assert.equal(calls.length, 4);
});

test('byte capacity evicts older recordings and oversized audio is played without being retained', async () => {
  const { calls, get } = resource({ maxBytes: 32, load: ({ text }) => Promise.resolve(audio('dictionary', text === 'large' ? 64 : 16)) });
  await get('one'); await get('two'); await get('three'); await get('two'); assert.equal(calls.length, 3);
  await get('one'); assert.equal(calls.length, 4);
  await get('large'); await get('large'); assert.equal(calls.length, 6);
  await get('one'); assert.equal(calls.length, 6, 'oversized audio must not discard an otherwise reusable recording');
});

test('learning identity changes clear audio and abort previous identity requests', async () => {
  let scope = 'guest-one'; const old = deferred();
  const { calls, get } = resource({ scope: async () => scope, load: ({ text }) => text === 'old' ? old.promise : Promise.resolve(audio()) });
  await get(); const prior = get('old'); await flush();
  scope = 'guest-two'; await get();
  assert.equal(calls.length, 3); assert.equal(calls[1].signal.aborted, true);
  old.resolve(audio()); await assert.rejects(prior, error => error.name === 'AbortError');
  scope = 'guest-one'; await get(); assert.equal(calls.length, 4);
});

test('a new request may replace an aborted identical request without its late cleanup deleting the replacement', async () => {
  const old = deferred(), next = deferred(), abort = new AbortController(); let count = 0;
  const { calls, get } = resource({ load: () => ++count === 1 ? old.promise : next.promise });
  const first = get('example', 'en-US', abort.signal); await flush();
  abort.abort(); await assert.rejects(first, error => error.name === 'AbortError');
  const second = get(); await flush(); old.resolve(audio()); await flush();
  const third = get(); await flush(); assert.equal(calls.length, 2);
  next.resolve(audio()); await second; await third;
});

test('clearing the session cache discards recordings and pending responses', async () => {
  const { cache, calls, get } = resource(); await get(); cache.clear(); await get();
  assert.equal(calls.length, 2);
});
