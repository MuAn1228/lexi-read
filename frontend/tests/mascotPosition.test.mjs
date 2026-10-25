import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/mascot/mascotPosition.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { clampMascot, snapMascot, findMascotPlacement, mascotRectsOverlap } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const viewport = { x: 0, y: 0, width: 1440, height: 900 };
const size = { width: 144, height: 170 };

test('clamp respects visual viewport offsets and never moves the saved input', () => {
  const saved = { x: -100, y: 9999 };
  assert.deepEqual(clampMascot(saved, size, { x: 14, y: 38, width: 390, height: 420 }), { x: 22, y: 280 });
  assert.deepEqual(saved, { x: -100, y: 9999 });
  assert.deepEqual(clampMascot({ x: 100, y: 100 }, size, { x: 0, y: 0, width: 30, height: 20 }), { x: 8, y: 8 });
});

test('release snaps only to left right bottom, retaining free positions in the middle', () => {
  assert.deepEqual(snapMascot({ x: 40, y: 200 }, size, viewport), { x: 24, y: 200, anchor: 'left', side: 'left' });
  assert.deepEqual(snapMascot({ x: 1250, y: 200 }, size, viewport), { x: 1272, y: 200, anchor: 'right', side: 'right' });
  assert.deepEqual(snapMascot({ x: 650, y: 710 }, size, viewport), { x: 650, y: 706, anchor: 'bottom', side: 'right' });
  assert.deepEqual(snapMascot({ x: 500, y: 10 }, size, viewport), { x: 500, y: 24, anchor: 'free', side: 'left' });
  assert.deepEqual(snapMascot({ x: 600, y: 300 }, size, viewport), { x: 600, y: 300, anchor: 'free', side: 'left' });
});

test('an unobstructed placement stays exactly where the user left it', () => {
  assert.deepEqual(findMascotPlacement({ x: 600, y: 300 }, size, viewport, []), { x: 600, y: 300 });
});

test('temporary avoidance finds nearby whitespace without overlapping reading or controls', () => {
  const obstacles = [{ x: 260, y: 280, width: 1130, height: 620 }, { x: 1250, y: 80, width: 150, height: 48 }];
  const preferred = { x: 1272, y: 706 };
  const point = findMascotPlacement(preferred, size, viewport, obstacles);
  assert.ok(point);
  assert.ok(obstacles.every(rect => !mascotRectsOverlap({ ...point, ...size }, rect, 4)));
  assert.deepEqual(preferred, { x: 1272, y: 706 });
});

test('crowded viewports explicitly report no clear space instead of covering protected surfaces', () => {
  const obstacles = [{ x: 0, y: 0, width: 1440, height: 900 }];
  assert.equal(findMascotPlacement({ x: 1272, y: 706 }, size, viewport, obstacles), null);
  assert.equal(findMascotPlacement({ x: 0, y: 0 }, { width: 20, height: 60 }, viewport, obstacles, 0), null);
});

test('a compact edge tab fits a narrow reading gutter when the full character cannot', () => {
  const narrow = { x: 0, y: 0, width: 390, height: 420 };
  const obstacles = [{ x: 28, y: 0, width: 334, height: 420 }];
  assert.equal(findMascotPlacement({ x: 270, y: 280 }, { width: 96, height: 113 }, narrow, obstacles), null);
  const tab = { width: 18, height: 64 };
  const point = findMascotPlacement({ x: 370, y: 300 }, tab, narrow, obstacles, 2);
  assert.ok(point);
  assert.ok(!mascotRectsOverlap({ ...point, ...tab }, obstacles[0], 4));
});

test('deterministic generated layouts always keep returned placements inside and clear', () => {
  let seed = 91271;
  const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
  for (let i = 0; i < 120; i++) {
    const screen = { x: random() * 12, y: random() * 30, width: 390 + random() * 900, height: 400 + random() * 500 };
    const obstacles = Array.from({ length: 12 }, () => ({ x: screen.x + random() * screen.width, y: screen.y + random() * screen.height, width: 30 + random() * 250, height: 20 + random() * 140 }));
    const point = findMascotPlacement({ x: random() * screen.width, y: random() * screen.height }, size, screen, obstacles);
    if (!point) continue;
    assert.deepEqual(clampMascot(point, size, screen), point);
    assert.ok(obstacles.every(rect => !mascotRectsOverlap({ ...point, ...size }, rect, 4)));
  }
});
