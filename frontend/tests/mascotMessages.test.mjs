import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/mascot/mascotMessages.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { mascotMessages, mascotSceneMessageGroup, chooseMascotMessage } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('authored dialogue stays concise and offers a varied pool for every scene and event', () => {
  const total = Object.values(mascotMessages).reduce((sum, lines) => sum + lines.length, 0);
  assert.ok(total >= 100 && total <= 140, `unexpected dialogue total ${total}`);
  for (const [group, lines] of Object.entries(mascotMessages)) {
    assert.ok(lines.length >= 3, `${group} needs multiple alternatives`);
    assert.equal(new Set(lines).size, lines.length, `duplicate ${group} lines`);
    for (const line of lines) {
      assert.equal(line, line.trim());
      assert.ok(Array.from(line).length <= 48, `${group} line is too long`);
      assert.ok(!/[<>\n\r]/u.test(line));
    }
  }
});

test('all application scenes have authored lines, with a safe general fallback', () => {
  for (const scene of ['reader', 'library', 'vocabulary', 'review', 'practice', 'history', 'dashboard', 'relations', 'data']) {
    assert.equal(mascotSceneMessageGroup(scene), scene);
    assert.ok(mascotMessages[mascotSceneMessageGroup(scene)].length >= 3);
  }
  assert.equal(mascotSceneMessageGroup(), 'idle');
  assert.equal(mascotSceneMessageGroup('unsupported-page'), 'idle');
});

test('recent lines remain excluded even when random repeats or the user leaves and returns', () => {
  const lines = mascotMessages.reader;
  const recent = lines.slice(0, 3);
  const next = chooseMascotMessage('reader', null, 0, 0, recent);
  assert.ok(!recent.includes(next));
  const exhausted = chooseMascotMessage('reader', lines[0], 0, 0, lines);
  assert.notEqual(exhausted, lines[0]);
  assert.deepEqual(recent, lines.slice(0, 3));
});

test('invalid randomness never selects missing text and rendered counts stay bounded', () => {
  for (const [group, lines] of Object.entries(mascotMessages)) {
    const expected = lines.map(line => line.replaceAll('{count}', '2'));
    for (const choice of [NaN, Infinity, -Infinity, -5, 0, 1, 500]) {
      assert.ok(expected.includes(chooseMascotMessage(group, null, choice, 2)), group);
    }
  }
  assert.match(chooseMascotMessage('recommendationSuccess', null, 0, 200), /3 个关联词/);
  assert.match(chooseMascotMessage('recommendationOffline', null, 0, NaN), /0 个离线关联词/);
  assert.doesNotMatch(chooseMascotMessage('recommendationOffline', null, 0, 1), /\{count\}/);
});
