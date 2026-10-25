import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const compile = source => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const url = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const position = url(compile(await readFile(new URL('../src/mascot/mascotPosition.ts', import.meta.url), 'utf8')));
const source = compile(await readFile(new URL('../src/mascot/mascotRestingPosition.ts', import.meta.url), 'utf8')).replace("'./mascotPosition'", JSON.stringify(position));
const { resolveMascotDock } = await import(url(source));
const viewport = { x: 0, y: 0, width: 1440, height: 900 };
const size = { width: 144, height: 170 };

test('both side preferences keep the whole character on the selected half when there is room', () => {
  const right = resolveMascotDock({ x: 20, y: 300 }, size, viewport, 'right');
  const left = resolveMascotDock({ x: 1300, y: 300 }, size, viewport, 'left');
  assert.deepEqual(right, { x: 728, y: 300 });
  assert.deepEqual(left, { x: 568, y: 300 });
  assert.ok(left.x + size.width <= viewport.width / 2);
  assert.ok(right.x >= viewport.width / 2);
});

test('left-side docking stays beside the navigation instead of behind it', () => {
  assert.deepEqual(resolveMascotDock({ x: 24, y: 500 }, size, viewport, 'left', 222), { x: 230, y: 500 });
  assert.deepEqual(resolveMascotDock({ x: 24, y: 500 }, size, { ...viewport, width: 800 }, 'left', 222), { x: 230, y: 500 });
});

test('visual viewport offsets and extreme saved coordinates are clamped without changing size', () => {
  const screen = { x: 14, y: 38, width: 390, height: 550 }, phoneSize = { width: 96, height: 113 };
  assert.deepEqual(resolveMascotDock({ x: 9999, y: 9999 }, phoneSize, screen, 'right'), { x: 300, y: 467 });
  assert.deepEqual(resolveMascotDock({ x: -9999, y: -9999 }, phoneSize, screen, 'left'), { x: 22, y: 46 });
});

test('narrow content and oversized sidebar still yield a visible full-size position', () => {
  const screen = { x: 0, y: 0, width: 340, height: 600 };
  assert.deepEqual(resolveMascotDock({ x: 24, y: 400 }, size, screen, 'left', 222), { x: 188, y: 400 });
  assert.deepEqual(resolveMascotDock({ x: 24, y: 400 }, size, screen, 'right', 9999), { x: 188, y: 400 });
});

test('resizing uses temporary bounds and returning to the original viewport restores saved placement', () => {
  const preferred = Object.freeze({ x: 1272, y: 706 });
  const compact = resolveMascotDock(preferred, size, { ...viewport, width: 768, height: 500 }, 'right', 0);
  assert.deepEqual(compact, { x: 616, y: 322 });
  assert.deepEqual(resolveMascotDock(preferred, size, viewport, 'right', 222), preferred);
  assert.deepEqual(preferred, { x: 1272, y: 706 });
});

test('fallback coordinates are finite and inputs remain immutable', () => {
  const preferred = Object.freeze({ x: NaN, y: Infinity });
  const frozenSize = Object.freeze({ ...size }), frozenScreen = Object.freeze({ ...viewport });
  assert.deepEqual(resolveMascotDock(preferred, frozenSize, frozenScreen, 'right', NaN), { x: 1288, y: 706 });
  assert.deepEqual(frozenSize, size); assert.deepEqual(frozenScreen, viewport);
  assert.ok(Number.isNaN(preferred.x)); assert.equal(preferred.y, Infinity);
});

test('generated normal viewports retain full-size bounds and avoid the sidebar', () => {
  let seed = 27317;
  const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
  for (let i = 0; i < 100; i++) {
    const screen = { x: random() * 20, y: random() * 40, width: 340 + random() * 1100, height: 400 + random() * 500 };
    const body = { width: screen.width <= 620 ? 96 : 144, height: screen.width <= 620 ? 113 : 170 };
    const sidebarRight = screen.width >= 800 ? screen.x + 222 : 0;
    const preferred = { x: random() * 2500 - 500, y: random() * 2500 - 500 };
    const side = i % 2 ? 'left' : 'right';
    const point = resolveMascotDock(preferred, body, screen, side, sidebarRight);
    assert.ok(point.x >= Math.max(screen.x + 8, sidebarRight + (sidebarRight ? 8 : 0)) - .001);
    assert.ok(point.x + body.width <= screen.x + screen.width - 8 + .001);
    assert.ok(point.y >= screen.y + 8 && point.y + body.height <= screen.y + screen.height - 8 + .001);
    if (side === 'right') assert.ok(point.x >= screen.x + screen.width / 2);
    else assert.ok(point.x + body.width <= screen.x + screen.width / 2);
  }
});
