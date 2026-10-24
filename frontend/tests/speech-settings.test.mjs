import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const compilerOptions = { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX };
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const speech = await readFile(new URL('../src/speech.ts', import.meta.url), 'utf8');
const speechURL = moduleURL(ts.transpileModule(speech, { compilerOptions }).outputText);
const hookURL = moduleURL('export const useSpeech = () => globalThis.__speechSettingsTestSnapshot;');
const source = await readFile(new URL('../src/PronunciationButton.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions }).outputText
  .replace(/from ['"]\.\/SpeechProvider['"]/g, `from '${hookURL}'`)
  .replace(/from ['"]\.\/speech['"]/g, `from '${speechURL}'`)
  .replace(/^import ['"]\.\/pronunciation\.css['"];?$/m, '')
  .replace(/from ['"](react(?:\/jsx-runtime)?|lucide-react)['"]/g, (_, name) => `from '${import.meta.resolve(name)}'`);
const { SpeechSettings } = await import(moduleURL(compiled));

function render(context, values = {}) {
  context.after(() => { delete globalThis.__speechSettingsTestSnapshot; });
  globalThis.__speechSettingsTestSnapshot = {
    accent: 'en-US', rate: 1, wordFallback: false, isSupported: true, voiceAvailable: true,
    engine: 'neural', source: null, voiceName: '', naturalAvailable: false,
    attribution: null, error: '', notice: '',
    setAccent() {}, setRate() {}, setWordFallback() {},
    ...values,
  };
  return renderToStaticMarkup(createElement(SpeechSettings));
}

test('Rosie-specific controls appear only when the backend confirms its local runtime', context => {
  const unavailable = render(context, { wordFallback: true });
  assert.match(unavailable, /无录音时使用合成朗读/);
  assert.match(unavailable, /type="checkbox" checked=""/);
  assert.doesNotMatch(unavailable, /Rosie|无录音时使用自然合成/);
  const available = render(context, { naturalAvailable: true });
  assert.match(available, /无录音时使用自然合成/);
  assert.match(available, /开启后，缺录音单词使用 Rosie 美式合成声线。/);
  assert.doesNotMatch(available, /checked=""/);
});

test('Rosie and legacy Piper playback are visibly distinguished and synthetic speech never shows recording credits', context => {
  const rosie = render(context, { source: 'kitten', voiceName: 'Rosie（美式）', naturalAvailable: true });
  assert.match(rosie, /本机神经语音<span>·<\/span>Rosie（美式）/);
  assert.doesNotMatch(rosie, /Commons 来源|speech-attribution/);
  const piper = render(context, { source: 'piper', voiceName: '英式英语' });
  assert.match(piper, /本机合成 · Piper<span>·<\/span>英式英语/);
  assert.doesNotMatch(piper, /本机神经语音|Rosie/);
});
