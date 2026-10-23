import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/speech.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { createSpeechController, readSpeechPreferences, selectSpeechVoice, speechTextProblem } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
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
  assert.equal(controller.getSnapshot().notice, '');
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
