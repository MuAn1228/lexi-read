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
