import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/speech.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { createSpeechController, readSpeechPreferences, restoreSpeechPreferences, selectSpeechVoice, speechTextProblem } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const us = { lang: 'en-US', name: 'US offline', localService: true, default: false };
const gb = { lang: 'en-GB', name: 'British offline', localService: true, default: false };

function engine(initial = [us, gb]) {
  const synth = new EventTarget();
  synth.voices = initial; synth.utterances = []; synth.cancels = 0; synth.paused = false;
  synth.getVoices = () => synth.voices;
  synth.speak = utterance => { synth.utterances.push(utterance); };
  synth.cancel = () => { synth.cancels++; };
  synth.resume = () => { synth.paused = false; };
  const controller = createSpeechController({ synthesis: synth, createUtterance: text => ({ text }) });
  return { synth, controller };
}

test('voice selection preserves requested accent and prefers an installed English voice', () => {
  const chineseDefault = { lang: 'zh-CN', default: true };
  assert.equal(selectSpeechVoice([chineseDefault, gb], 'en-US'), null);
  assert.equal(selectSpeechVoice([chineseDefault, { ...us, localService: false, default: true }, us], 'en-US'), us);
  assert.equal(selectSpeechVoice([{ ...gb, lang: 'en_GB' }], 'en-GB').name, gb.name);
});

test('empty asynchronous voice lists are refreshed and listener cleanup can reconnect', () => {
  const { synth, controller } = engine([]);
  const disconnect = controller.connect();
  assert.equal(controller.getSnapshot().voiceAvailable, false);
  synth.voices = [us]; synth.dispatchEvent(new Event('voiceschanged'));
  assert.equal(controller.getSnapshot().voiceAvailable, true);
  disconnect();
  synth.voices = []; synth.dispatchEvent(new Event('voiceschanged'));
  assert.equal(controller.getSnapshot().voiceAvailable, true, 'disconnected listener must not update');
  const disconnectAgain = controller.connect();
  assert.equal(controller.getSnapshot().voiceAvailable, false);
  disconnectAgain();
});

test('rapid word-to-sentence switches cancel the queue and ignore late old callbacks', () => {
  const { synth, controller } = engine();
  controller.speak('curious', { key: 'word' });
  const first = synth.utterances.at(-1);
  const oldStart = first.onstart, oldEnd = first.onend, oldError = first.onerror;
  controller.speak('A curious reader explores the garden.', { key: 'sentence' });
  const second = synth.utterances.at(-1);
  second.onstart();
  oldStart(); oldEnd(); oldError({ error: 'network' });
  assert.equal(controller.getSnapshot().activeKey, 'sentence');
  assert.equal(controller.getSnapshot().status, 'speaking');
  assert.equal(controller.getSnapshot().error, '');
  assert.equal(first.onstart, null);
  assert.equal(first.onend, null);
  assert.equal(first.onerror, null);
  controller.stop('word');
  assert.equal(controller.getSnapshot().activeKey, 'sentence', 'a removed old button must not stop a new button');
  controller.stop('sentence');
  assert.equal(controller.getSnapshot().activeKey, null);
  assert.ok(synth.cancels >= 3);
});

test('accent and rate changes stop current speech and apply to the next utterance', () => {
  const { synth, controller } = engine();
  controller.speak('habit', { key: 'word' });
  controller.setAccent('en-GB');
  assert.equal(controller.getSnapshot().activeKey, null);
  controller.setRate(0.8);
  controller.speak('A strong habit helps.', { key: 'sentence' });
  const utterance = synth.utterances.at(-1);
  assert.equal(utterance.voice, gb);
  assert.equal(utterance.lang, 'en-GB');
  assert.equal(utterance.rate, 0.8);
  assert.equal(utterance.pitch, 1);
  assert.equal(utterance.volume, 1);
  utterance.onend();
  assert.equal(controller.getSnapshot().status, 'idle');
});

test('missing chosen voice is explicit and never queues speech with the system default', () => {
  const { synth, controller } = engine([gb]);
  controller.speak('hello');
  assert.equal(synth.utterances.length, 0);
  assert.match(controller.getSnapshot().error, /没有所选口音/);
  controller.setAccent('en-GB');
  assert.equal(controller.getSnapshot().error, '');
  controller.speak('hello');
  assert.equal(synth.utterances.length, 1);
  controller.stop();
});

test('browser audio errors clear active playback and retries do not retain the failure', () => {
  const { synth, controller } = engine();
  controller.speak('hello');
  synth.utterances.at(-1).onerror({ error: 'not-allowed' });
  assert.equal(controller.getSnapshot().activeKey, null);
  assert.equal(controller.getSnapshot().status, 'error');
  assert.match(controller.getSnapshot().error, /点击/);
  controller.speak('hello');
  assert.equal(controller.getSnapshot().error, '');
  synth.utterances.at(-1).onerror({ error: 'interrupted' });
  assert.equal(controller.getSnapshot().status, 'idle');
  assert.equal(controller.getSnapshot().error, '');
});

test('a disconnected or removed voice cancels playing audio', () => {
  const { synth, controller } = engine();
  const disconnect = controller.connect();
  controller.speak('hello');
  synth.voices = [gb]; synth.dispatchEvent(new Event('voiceschanged'));
  assert.equal(controller.getSnapshot().activeKey, null);
  assert.equal(controller.getSnapshot().voiceAvailable, false);
  synth.voices = [us]; synth.dispatchEvent(new Event('voiceschanged'));
  controller.speak('hello');
  disconnect();
  assert.equal(controller.getSnapshot().activeKey, null);
});

test('unsupported and broken engines fail safely and preference storage is optional', () => {
  const unsupported = createSpeechController({ synthesis: null, createUtterance: null });
  unsupported.speak('hello');
  assert.equal(unsupported.getSnapshot().isSupported, false);
  assert.match(unsupported.getSnapshot().error, /不支持/);
  const { synth } = engine();
  synth.speak = () => { throw new Error('private platform diagnostic'); };
  const controller = createSpeechController({ synthesis: synth, createUtterance: text => ({ text }), onPreferences: () => { throw new Error('storage unavailable'); } });
  controller.setRate(0.8);
  controller.speak('hello');
  assert.equal(controller.getSnapshot().activeKey, null);
  assert.match(controller.getSnapshot().error, /无法启动/);
  assert.equal(controller.getSnapshot().error.includes('private'), false);
});

test('preference parsing ignores malformed input and speech length counts Unicode characters', () => {
  for (const raw of [null, '{}', 'null', '[]', 'true', 'bad json', '{"rate":0,"accent":"zh-CN"}']) {
    assert.deepEqual(readSpeechPreferences(raw), { accent: 'en-US', rate: 1 });
  }
  assert.deepEqual(readSpeechPreferences('{"rate":0.8,"accent":"en-GB"}'), { accent: 'en-GB', rate: 0.8 });
  assert.equal(speechTextProblem('hello ' + '🌱'.repeat(3_994)), '');
  assert.match(speechTextProblem('hello ' + '🌱'.repeat(3_995)), /4,000/);
  assert.match(speechTextProblem(' \n\t'), /没有/);
});

test('a recalibrated voice resets legacy speed once and keeps the selected accent', () => {
  for (const rate of [0.8, 1, 1.2]) {
    for (const accent of ['en-US', 'en-GB']) {
      assert.deepEqual(restoreSpeechPreferences(null, JSON.stringify({ accent, rate })), { accent, rate: 1 });
    }
  }
  assert.deepEqual(restoreSpeechPreferences(null, null), { accent: 'en-US', rate: 1 });
  assert.deepEqual(restoreSpeechPreferences(null, 'unreadable'), { accent: 'en-US', rate: 1 });
});

test('saved recalibrated preferences take precedence and preserve later speed choices across reloads', () => {
  const legacy = JSON.stringify({ accent: 'en-GB', rate: 1.2 });
  for (const rate of [0.8, 1, 1.2]) {
    const current = JSON.stringify({ accent: 'en-US', rate });
    assert.deepEqual(restoreSpeechPreferences(current, legacy), { accent: 'en-US', rate });
    assert.deepEqual(restoreSpeechPreferences(JSON.stringify(restoreSpeechPreferences(current, legacy)), legacy), { accent: 'en-US', rate });
  }
  assert.deepEqual(restoreSpeechPreferences('{"accent":"en-GB","rate":0.8}', null), { accent: 'en-GB', rate: 0.8 });
});

test('invalid recalibrated preferences use safe defaults instead of recovering the old fast speed', () => {
  const legacy = '{"accent":"en-GB","rate":1.2}';
  for (const current of ['', 'bad json', 'null', '[]', 'true', '{}', '{"accent":"zh-CN","rate":9}']) {
    assert.deepEqual(restoreSpeechPreferences(current, legacy), { accent: 'en-US', rate: 1 });
  }
  assert.deepEqual(restoreSpeechPreferences('{"accent":"en-GB","rate":1.3}', legacy), { accent: 'en-GB', rate: 1 });
});

test('missing storage still starts at normal speed and rejected preference writes do not prevent speech', () => {
  const { synth } = engine();
  const controller = createSpeechController({
    synthesis: synth,
    createUtterance: text => ({ text }),
    preferences: restoreSpeechPreferences(null, null),
    onPreferences: () => { throw new Error('storage unavailable'); },
  });
  assert.equal(controller.getSnapshot().rate, 1);
  controller.setAccent('en-GB');
  controller.setRate(0.8);
  controller.speak('curious');
  assert.equal(synth.utterances.at(-1).lang, 'en-GB');
  assert.equal(synth.utterances.at(-1).rate, 0.8);
  controller.stop();
});

test('a silently unresponsive engine times out and cannot revive after a retry', context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const { synth, controller } = engine();
  controller.speak('hello', { key: 'old' });
  const lateStart = synth.utterances.at(-1).onstart;
  context.mock.timers.tick(12_000);
  assert.equal(controller.getSnapshot().activeKey, null);
  assert.match(controller.getSnapshot().error, /没有响应/);
  controller.speak('next', { key: 'new' });
  lateStart();
  assert.equal(controller.getSnapshot().activeKey, 'new');
  assert.equal(controller.getSnapshot().status, 'starting');
  synth.utterances.at(-1).onstart();
  context.mock.timers.tick(30_000);
  assert.equal(controller.getSnapshot().activeKey, null);
  assert.match(controller.getSnapshot().error, /中断/);
  controller.stop();
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };
const wav = () => new Blob(['public-example-wave'], { type: 'audio/wav' });

function naturalEngine(context, { voices = [us, gb], load, play, system = true, create } = {}) {
  const { synth } = engine(voices);
  const requests = [], players = [], urls = [], revoked = [];
  const controller = createSpeechController({
    synthesis: system ? synth : null, createUtterance: system ? text => ({ text }) : null,
    neuralAvailable: true,
    loadAudio: (text, accent, signal) => {
      const request = { text, accent, signal }; requests.push(request);
      return load ? load(request) : Promise.resolve(wav());
    },
    createAudio: url => {
      if (create) return create(url);
      const player = { src: url, playbackRate: 1, preservesPitch: false, pauses: 0, plays: 0,
        onplaying: null, onended: null, onerror: null,
        pause() { this.pauses++; },
        play() { this.plays++; return play ? play(player) : Promise.resolve(); },
      };
      players.push(player); return player;
    },
    createObjectURL: blob => { assert.ok(blob.size); const url = `blob:public-test-${urls.length}`; urls.push(url); return url; },
    revokeObjectURL: url => { revoked.push(url); },
  });
  context.after(() => controller.stop());
  return { synth, controller, requests, players, urls, revoked };
}

test('system voice selection prefers natural voices and Zira over a first-listed Mark', () => {
  const mark = { ...us, name: 'Microsoft Mark - English (United States)' };
  const zira = { ...us, name: 'Microsoft Zira - English (United States)' };
  const natural = { ...us, localService: false, name: 'English Natural Voice' };
  assert.equal(selectSpeechVoice([mark, zira], 'en-US'), zira);
  assert.equal(selectSpeechVoice([mark, zira, natural], 'en-US'), natural);
  assert.equal(selectSpeechVoice([natural, gb], 'en-GB'), gb);
});

test('natural audio supports British English even without Web Speech or an installed British voice', async context => {
  const { controller, requests, players } = naturalEngine(context, { voices: [], system: false });
  assert.equal(controller.getSnapshot().isSupported, true);
  controller.setAccent('en-GB');
  assert.equal(controller.getSnapshot().voiceAvailable, true);
  controller.speak('curious', { key: 'word' }); await flush();
  assert.equal(requests[0].accent, 'en-GB');
  assert.equal(players[0].plays, 1);
  assert.equal(controller.getSnapshot().engine, 'neural');
  assert.equal(controller.getSnapshot().voiceName, '英式英语');
});

test('natural audio uses one normal-speed recording and adjusts playback without changing pitch', async context => {
  const { controller, requests, players, revoked } = naturalEngine(context);
  for (const rate of [0.8, 1, 1.2]) {
    controller.setRate(rate); controller.speak('A public example.', { key: 'sentence' }); await flush();
    assert.equal(players.at(-1).playbackRate, rate);
    assert.equal(players.at(-1).preservesPitch, true);
    assert.deepEqual(Object.keys(requests.at(-1)).sort(), ['accent', 'signal', 'text']);
    assert.equal(controller.getSnapshot().status, 'speaking');
  }
  controller.stop();
  assert.equal(revoked.length, 3);
  assert.ok(players.every(player => player.pauses >= 1 && player.src === ''));
});

test('late old audio responses cannot create players or replace a newer word', async context => {
  const first = deferred(), second = deferred();
  let count = 0;
  const { controller, requests, players, urls } = naturalEngine(context, { load: () => (++count === 1 ? first : second).promise });
  controller.speak('first', { key: 'old' });
  controller.speak('second', { key: 'new' });
  assert.equal(requests[0].signal.aborted, true);
  second.resolve(wav()); await flush();
  first.resolve(wav()); await flush();
  assert.equal(players.length, 1); assert.equal(urls.length, 1);
  assert.equal(controller.getSnapshot().activeKey, 'new');
  assert.equal(controller.getSnapshot().status, 'speaking');
});

test('stopping pending audio aborts the request and its eventual result remains silent', async context => {
  const reply = deferred();
  const { controller, requests, players, urls } = naturalEngine(context, { load: () => reply.promise });
  controller.speak('public example', { key: 'word' });
  controller.stop('unrelated'); assert.equal(requests[0].signal.aborted, false);
  controller.stop('word'); assert.equal(requests[0].signal.aborted, true);
  reply.resolve(wav()); await flush();
  assert.equal(players.length, 0); assert.equal(urls.length, 0);
  assert.equal(controller.getSnapshot().activeKey, null);
});

test('a late old play promise and callbacks cannot revive canceled audio or stop its replacement', async context => {
  const oldPlay = deferred(); let count = 0;
  const { controller, players, revoked } = naturalEngine(context, { play: () => (++count === 1 ? oldPlay.promise : Promise.resolve()) });
  controller.speak('first', { key: 'old' }); await flush();
  const first = players[0], oldPlaying = first.onplaying, oldEnd = first.onended, oldError = first.onerror;
  controller.speak('second', { key: 'new' }); await flush();
  assert.equal(first.onplaying, null); assert.equal(first.src, '');
  oldPlaying(); oldEnd(); oldError(); oldPlay.resolve(); await flush();
  assert.ok(first.pauses >= 2); assert.deepEqual(revoked, ['blob:public-test-0']);
  assert.equal(controller.getSnapshot().activeKey, 'new');
  assert.equal(controller.getSnapshot().engine, 'neural');
});

test('a failed natural request clearly falls back to the same accent and the next click retries natural', async context => {
  let count = 0;
  const { controller, synth, requests } = naturalEngine(context, { load: () => ++count === 1 ? Promise.reject(new Error('private server failure')) : Promise.resolve(wav()) });
  controller.speak('curious'); await flush();
  assert.equal(synth.utterances.length, 1);
  assert.equal(controller.getSnapshot().engine, 'system');
  assert.equal(controller.getSnapshot().voiceName, us.name);
  assert.match(controller.getSnapshot().notice, /已回退.*再次点击/);
  assert.equal(controller.getSnapshot().notice.includes('private'), false);
  synth.utterances[0].onend();
  controller.speak('curious'); await flush();
  assert.equal(requests.length, 2);
  assert.equal(controller.getSnapshot().engine, 'neural');
  assert.match(controller.getSnapshot().notice, /暂无.*录音.*本机合成/);
});

test('natural failure with no matching British voice never substitutes an American voice', async context => {
  const { controller, synth, players } = naturalEngine(context, { voices: [us], load: () => Promise.reject(new Error('offline')) });
  controller.setAccent('en-GB'); controller.speak('hello'); await flush();
  assert.equal(synth.utterances.length, 0); assert.equal(players.length, 0);
  assert.equal(controller.getSnapshot().status, 'error');
  assert.match(controller.getSnapshot().error, /没有所选口音/);
});

test('autoplay denial reports the click requirement and never bypasses it through system speech', async context => {
  let count = 0;
  const { controller, synth, players, revoked } = naturalEngine(context, { play: () => ++count === 1 ? Promise.reject(new DOMException('gesture required', 'NotAllowedError')) : Promise.resolve() });
  controller.speak('hello'); await flush();
  assert.match(controller.getSnapshot().error, /点击/);
  assert.equal(controller.getSnapshot().status, 'error');
  assert.equal(synth.utterances.length, 0);
  assert.equal(players[0].src, ''); assert.equal(revoked.length, 1);
  controller.speak('hello'); await flush();
  assert.equal(controller.getSnapshot().status, 'speaking');
  assert.equal(controller.getSnapshot().error, '');
});

test('audio decoding failure revokes its URL and leaves the system fallback in control', async context => {
  const play = deferred();
  const { controller, synth, players, revoked } = naturalEngine(context, { play: () => play.promise });
  controller.speak('hello', { key: 'word' }); await flush();
  const player = players[0], latePlaying = player.onplaying;
  player.onerror();
  assert.equal(synth.utterances.length, 1); assert.equal(revoked.length, 1);
  synth.utterances[0].onstart();
  latePlaying(); play.resolve(); await flush();
  assert.equal(controller.getSnapshot().engine, 'system');
  assert.equal(controller.getSnapshot().status, 'speaking');
});

test('disconnect, accent changes, and restoring normal speed cancel natural playback completely', async context => {
  const { controller, players, requests, revoked } = naturalEngine(context);
  const disconnect = controller.connect();
  controller.speak('first'); await flush(); controller.setAccent('en-GB');
  assert.equal(players[0].src, ''); assert.equal(requests[0].signal.aborted, true);
  controller.setRate(0.8); controller.speak('second'); await flush(); controller.setRate(1);
  assert.equal(players[1].src, ''); assert.equal(controller.getSnapshot().rate, 1);
  controller.speak('third'); await flush(); disconnect();
  assert.equal(players[2].src, ''); assert.equal(revoked.length, 3);
  assert.equal(controller.getSnapshot().activeKey, null);
});

test('removing system voices does not interrupt a playing natural recording', async context => {
  const { controller, synth, players } = naturalEngine(context);
  const disconnect = controller.connect();
  controller.speak('hello'); await flush();
  synth.voices = []; synth.dispatchEvent(new Event('voiceschanged'));
  assert.equal(controller.getSnapshot().status, 'speaking');
  assert.equal(controller.getSnapshot().voiceAvailable, true);
  assert.equal(players[0].pauses, 0);
  disconnect();
});

test('a late service status does not relabel already playing system speech as natural audio', async context => {
  const { controller, synth } = naturalEngine(context);
  controller.setNeuralAvailable(false, '本机自然语音尚未安装。');
  controller.speak('hello'); synth.utterances.at(-1).onstart();
  controller.setNeuralAvailable(true);
  assert.equal(controller.getSnapshot().engine, 'system');
  assert.equal(controller.getSnapshot().voiceName, us.name);
  assert.equal(controller.getSnapshot().status, 'speaking');
  controller.stop();
  assert.equal(controller.getSnapshot().engine, 'neural');
  assert.equal(controller.getSnapshot().notice, '');
});

test('natural preparation timeout aborts the request and ignores a very late response', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const reply = deferred();
  const { controller, requests, players } = naturalEngine(context, { load: () => reply.promise });
  controller.speak('hello'); context.mock.timers.tick(180_000);
  assert.equal(requests[0].signal.aborted, true);
  assert.match(controller.getSnapshot().error, /准备超时/);
  reply.resolve(wav()); await flush();
  assert.equal(players.length, 0);
  assert.equal(controller.getSnapshot().status, 'error');
});

test('empty audio and player construction errors release resources before a safe fallback', async context => {
  const empty = naturalEngine(context, { load: () => Promise.resolve(new Blob()) });
  empty.controller.speak('hello'); await flush();
  assert.equal(empty.players.length, 0); assert.equal(empty.synth.utterances.length, 1);
  const broken = naturalEngine(context, { create: () => { throw new Error('private device data'); } });
  broken.controller.speak('hello'); await flush();
  assert.equal(broken.revoked.length, 1); assert.equal(broken.synth.utterances.length, 1);
  assert.equal(broken.controller.getSnapshot().notice.includes('private'), false);
});

const recordingAttribution = {
  author: 'Public example recorder', license: 'CC BY-SA 4.0',
  source_url: 'https://commons.wikimedia.org/wiki/File:En-us-example.ogg',
  license_url: 'https://creativecommons.org/licenses/by-sa/4.0/',
};
const recorded = () => ({ blob: wav(), source: 'dictionary', attribution: { ...recordingAttribution } });

test('a verified dictionary recording changes source only after its own response and uses the selected playback rate', async context => {
  const response = deferred();
  const { controller, players } = naturalEngine(context, { load: () => response.promise });
  controller.speak('example');
  assert.notEqual(controller.getSnapshot().engine, 'dictionary');
  assert.equal(controller.getSnapshot().voiceName, '');
  assert.equal(controller.getSnapshot().attribution, null);
  response.resolve(recorded()); await flush();
  assert.equal(controller.getSnapshot().engine, 'dictionary');
  assert.equal(controller.getSnapshot().voiceName, '美式英语');
  assert.deepEqual(controller.getSnapshot().attribution, recordingAttribution);
  assert.equal(players[0].playbackRate, 1);
  assert.equal(players[0].preservesPitch, true);
  players[0].onended();
  assert.equal(controller.getSnapshot().engine, 'dictionary', 'recording credits stay available after playback');
  assert.deepEqual(controller.getSnapshot().attribution, recordingAttribution);
});

test('new requests clear old recording credits and aborted late metadata cannot replace the current source', async context => {
  const late = deferred(), current = deferred(); let count = 0;
  const { controller } = naturalEngine(context, { load: () => ++count === 1 ? Promise.resolve(recorded()) : count === 2 ? late.promise : current.promise });
  controller.speak('example'); await flush();
  assert.equal(controller.getSnapshot().engine, 'dictionary');
  controller.speak('older');
  assert.equal(controller.getSnapshot().attribution, null);
  assert.equal(controller.getSnapshot().voiceName, '');
  controller.speak('A public example sentence.');
  current.resolve({ blob: wav(), source: 'piper' }); await flush();
  late.resolve(recorded()); await flush();
  assert.equal(controller.getSnapshot().engine, 'neural');
  assert.equal(controller.getSnapshot().attribution, null);
  assert.equal(controller.getSnapshot().notice, '');
});

test('a local synthesis fallback for a single word is explicit while a normal sentence needs no warning', async context => {
  const { controller } = naturalEngine(context, { load: () => Promise.resolve({ blob: wav(), source: 'piper' }) });
  controller.speak('example'); await flush();
  assert.match(controller.getSnapshot().notice, /此词暂无.*所选口音录音.*本机合成/);
  controller.speak('A public example.'); await flush();
  assert.equal(controller.getSnapshot().notice, '');
  assert.equal(controller.getSnapshot().engine, 'neural');
  assert.equal(controller.getSnapshot().attribution, null);
});

test('stop, accent changes and system fallbacks never retain dictionary recording credits', async context => {
  let count = 0;
  const { controller, synth } = naturalEngine(context, { load: () => ++count === 3 ? Promise.reject(new Error('offline')) : Promise.resolve(recorded()) });
  controller.speak('example'); await flush(); controller.stop();
  assert.equal(controller.getSnapshot().attribution, null);
  controller.speak('example'); await flush(); controller.setAccent('en-GB');
  assert.equal(controller.getSnapshot().attribution, null);
  controller.speak('example'); await flush();
  assert.equal(controller.getSnapshot().engine, 'system');
  assert.equal(controller.getSnapshot().attribution, null);
  assert.equal(synth.utterances.at(-1).lang, 'en-GB');
});

test('an uncredited dictionary result cannot be labelled as a usable recording', async context => {
  const { controller, players, synth } = naturalEngine(context, { load: () => Promise.resolve({ blob: wav(), source: 'dictionary' }) });
  controller.speak('example'); await flush();
  assert.equal(players.length, 0);
  assert.equal(synth.utterances.length, 1);
  assert.equal(controller.getSnapshot().engine, 'system');
  assert.equal(controller.getSnapshot().attribution, null);
});

const apiSource = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');
let apiModule = 0;
async function pronunciationAPI(context, respond, guest = false) {
  const saved = { fetch: globalThis.fetch, window: globalThis.window, storage: globalThis.localStorage };
  context.after(() => {
    globalThis.fetch = saved.fetch; globalThis.window = saved.window; globalThis.localStorage = saved.storage;
  });
  globalThis.window = { location: { origin: 'https://reader.example.com' } };
  const stored = new Map();
  globalThis.localStorage = { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key) };
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith('/health')) return new Response(JSON.stringify({ auth_mode: guest ? 'guest' : 'local' }), { headers: { 'Content-Type': 'application/json' } });
    if (url.endsWith('/session')) return new Response(JSON.stringify({ token: 'public-test-identity' }), { headers: { 'Content-Type': 'application/json' } });
    return respond(url, init);
  };
  const environment = JSON.stringify({ VITE_API_ORIGIN: 'https://api.example.com', BASE_URL: '/lexi-read/' });
  const output = ts.transpileModule(apiSource.replaceAll('import.meta.env', `(${environment})`), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  const client = await import(`data:text/javascript;base64,${Buffer.from(`${output}\n// pronunciation ${++apiModule}`).toString('base64')}`);
  return { client, calls };
}

function pronunciationResponse(source = 'dictionary', attribution = recordingAttribution) {
  const headers = { 'Content-Type': 'audio/wav' };
  if (source !== null) headers['X-Pronunciation-Source'] = source;
  if (attribution !== null) headers['X-Pronunciation-Attribution'] = typeof attribution === 'string' ? attribution : JSON.stringify(attribution);
  return new Response(wav(), { headers });
}

test('pronunciation requests authenticate one fixed backend path and return verified recording credits', async context => {
  const abort = new AbortController();
  const { client, calls } = await pronunciationAPI(context, (url, init) => {
    assert.equal(url, 'https://api.example.com/api/pronunciation');
    assert.equal(init.method, 'POST');
    assert.equal(init.headers.get('Authorization'), 'Bearer public-test-identity');
    assert.deepEqual(JSON.parse(init.body), { text: 'example', accent: 'en-GB' });
    assert.equal(init.signal.aborted, false);
    return pronunciationResponse();
  }, true);
  const result = await client.requestPronunciation('example', 'en-GB', abort.signal);
  assert.equal(result.source, 'dictionary');
  assert.deepEqual(result.attribution, recordingAttribution);
  assert.ok(result.blob.size);
  assert.ok(calls.every(call => !call.url.includes('public-test-identity')));
});

test('unsafe source links, license links and disguised HTML credits are rejected before dictionary playback', async context => {
  let attribution = recordingAttribution;
  const { client } = await pronunciationAPI(context, () => pronunciationResponse('dictionary', attribution));
  const bad = [
    null, 'bad json', '[]', '{}',
    { ...recordingAttribution, author: '<a href="https://evil.example">Recorder</a>' },
    { ...recordingAttribution, license: '<script>alert(1)</script>' },
    { ...recordingAttribution, author: 'Recorder\nInjected header' },
    { ...recordingAttribution, license: 'x'.repeat(121) },
    ...['javascript:alert(1)', 'http://commons.wikimedia.org/wiki/File:Word.ogg', 'https://commons.wikimedia.org.evil.example/wiki/File:Word.ogg', 'https://commons.wikimedia.org@evil.example/wiki/File:Word.ogg', 'https://commons.wikimedia.org/wiki/User:Recorder', 'https://commons.wikimedia.org/wiki/File:Word.ogg?redirect=evil', 'https://commons.wikimedia.org:444/wiki/File:Word.ogg'].map(source_url => ({ ...recordingAttribution, source_url })),
    ...['javascript:alert(1)', 'https://creativecommons.org.evil.example/licenses/by/4.0/', 'https://evil.example/licenses/by/4.0/', 'https://creativecommons.org/about/', 'https://creativecommons.org/licenses/by/4.0/#injected'].map(license_url => ({ ...recordingAttribution, license_url })),
  ];
  for (attribution of bad) await assert.rejects(client.requestPronunciation('example', 'en-US'), /来源信息不完整/);
});

test('synthesized audio never receives dictionary credits and unknown audio sources cannot impersonate Piper', async context => {
  let source = 'piper';
  const { client } = await pronunciationAPI(context, () => pronunciationResponse(source, { ...recordingAttribution, source_url: 'javascript:alert(1)' }));
  const synthesized = await client.requestPronunciation('A public example.', 'en-US');
  assert.equal(synthesized.source, 'piper');
  assert.equal(synthesized.attribution, undefined);
  for (source of [null, '', 'system', 'natural', '<a>dictionary</a>']) await assert.rejects(client.requestPronunciation('example', 'en-US'), /来源信息不完整/);
});

test('a canceled pronunciation request cannot fetch audio or recover external metadata', async context => {
  const { client, calls } = await pronunciationAPI(context, () => { throw new Error('audio fetch must not happen'); });
  const abort = new AbortController(); abort.abort();
  await assert.rejects(client.requestPronunciation('example', 'en-US', abort.signal), error => error.name === 'AbortError');
  assert.equal(calls.some(call => call.url.endsWith('/pronunciation')), false);
});

test('preparing one word is silent and does not change the active source or playback state', async context => {
  const response = deferred();
  const { controller, requests, players, synth, urls } = naturalEngine(context, { load: () => response.promise });
  const before = controller.getSnapshot();
  controller.prepare('example', { key: 'current-word' });
  assert.equal(requests.length, 1);
  assert.equal(controller.getSnapshot(), before);
  response.resolve(recorded()); await flush();
  assert.equal(players.length, 0); assert.equal(urls.length, 0); assert.equal(synth.utterances.length, 0);
  assert.equal(controller.getSnapshot(), before);
});

test('preparation only accepts single words and never scans a sentence or calls an unavailable audio service', async context => {
  const { controller, requests } = naturalEngine(context);
  for (const text of ['', 'two words', 'A public example sentence.', '123', 'a'.repeat(81), '你好']) controller.prepare(text);
  assert.equal(requests.length, 0);
  controller.setNeuralAvailable(false); controller.prepare('example');
  assert.equal(requests.length, 0);
  controller.setNeuralAvailable(true); controller.prepare('well-known'); await flush();
  assert.equal(requests.length, 1);
});

test('new word preparation, stop, hidden-page cleanup and disconnect abort old work without allowing late playback', async context => {
  const response = deferred();
  const { controller, requests, players, synth } = naturalEngine(context, { load: () => response.promise });
  const disconnect = controller.connect();
  controller.prepare('first', { key: 'first' }); controller.prepare('second', { key: 'second' });
  assert.equal(requests[0].signal.aborted, true);
  controller.cancelPreparation('first'); assert.equal(requests[1].signal.aborted, false);
  controller.stop('second'); assert.equal(requests[1].signal.aborted, true);
  controller.prepare('third'); controller.stop(); assert.equal(requests[2].signal.aborted, true);
  controller.prepare('fourth'); disconnect(); assert.equal(requests[3].signal.aborted, true);
  response.resolve(recorded()); await flush();
  assert.equal(players.length, 0); assert.equal(synth.utterances.length, 0);
});

test('preparation failure is silent and never falls back to a system voice', async context => {
  const { controller, players, synth } = naturalEngine(context, { load: () => Promise.reject(new Error('offline')) });
  controller.prepare('example'); await flush();
  assert.equal(controller.getSnapshot().status, 'idle'); assert.equal(controller.getSnapshot().error, '');
  assert.equal(players.length, 0); assert.equal(synth.utterances.length, 0);
});
