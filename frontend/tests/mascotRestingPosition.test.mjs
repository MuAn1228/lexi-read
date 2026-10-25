import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const compile = source => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const url = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const position = url(compile(await readFile(new URL('../src/mascot/mascotPosition.ts', import.meta.url), 'utf8')));
const source = compile(await readFile(new URL('../src/mascot/mascotRestingPosition.ts', import.meta.url), 'utf8')).replace("'./mascotPosition'", JSON.stringify(position));
const { findMascotRestingPlacement, findMascotEdgePlacement } = await import(url(source));
const { mascotRectsOverlap } = await import(position);
const viewport = { x: 0, y: 0, width: 1440, height: 900 };

test('long reading panels cannot send a right-side character into a clear left gutter', () => {
  const obstacles = [{ x: 0, y: 0, width: 1440, height: 78 }, { x: 270, y: 80, width: 1170, height: 820 }, { x: 16, y: 80, width: 190, height: 820 }];
  for (const size of [{ width: 144, height: 170 }, { width: 80, height: 94 }]) {
    assert.equal(findMascotRestingPlacement({ x: 1272, y: 706 }, size, viewport, obstacles, 'right'), null);
  }
  assert.equal(findMascotEdgePlacement(706, { width: 18, height: 64 }, viewport, obstacles, 'right'), null);
});

test('resting placement keeps the entire full or miniature character on the selected half', () => {
  for (const size of [{ width: 144, height: 170 }, { width: 80, height: 94 }]) {
    const right = findMascotRestingPlacement({ x: 20, y: 300 }, size, viewport, [], 'right');
    const left = findMascotRestingPlacement({ x: 1300, y: 300 }, size, viewport, [], 'left');
    assert.ok(right.x >= 728 && right.x + size.width <= 1432);
    assert.ok(left.x >= 8 && left.x + size.width <= 712);
    assert.equal(right.y, 300); assert.equal(left.y, 300);
  }
});

test('blocked chosen edges never fall back to the opposite clear edge', () => {
  const size = { width: 18, height: 64 };
  assert.equal(findMascotEdgePlacement(500, size, viewport, [{ x: 1200, y: 0, width: 240, height: 900 }], 'right'), null);
  assert.equal(findMascotEdgePlacement(500, size, viewport, [{ x: 0, y: 0, width: 240, height: 900 }], 'left'), null);
  assert.deepEqual(findMascotEdgePlacement(500, size, viewport, [], 'right'), { x: 1420, y: 500 });
  assert.deepEqual(findMascotEdgePlacement(500, size, viewport, [], 'left'), { x: 2, y: 500 });
});

test('an edge handle avoids protected controls by changing only its vertical coordinate', () => {
  const size = { width: 18, height: 64 };
  const obstacles = [{ x: 0, y: 0, width: 1440, height: 78 }, { x: 1400, y: 300, width: 40, height: 200 }];
  const point = findMascotEdgePlacement(400, size, viewport, obstacles, 'right');
  assert.deepEqual(point, { x: 1420, y: 504 });
  for (const obstacle of obstacles) assert.equal(mascotRectsOverlap({ ...point, ...size }, obstacle, 4), false);
});

test('visual viewport offsets and extreme saved y values preserve the physical chosen edge', () => {
  const screen = { x: 14, y: 38, width: 390, height: 550 }, size = { width: 12, height: 64 };
  assert.deepEqual(findMascotEdgePlacement(9999, size, screen, [], 'right'), { x: 390, y: 522 });
  assert.deepEqual(findMascotEdgePlacement(-9999, size, screen, [], 'left'), { x: 16, y: 40 });
  const point = findMascotRestingPlacement({ x: 20, y: 200 }, { width: 64, height: 76 }, screen, [], 'right');
  assert.equal(point.x, 217); assert.equal(point.y, 200);
});

test('too-small viewports and invalid geometry return no placement instead of overflowing', () => {
  assert.equal(findMascotRestingPlacement({ x: 0, y: 0 }, { width: 80, height: 94 }, { x: 0, y: 0, width: 180, height: 900 }, [], 'right'), null);
  assert.equal(findMascotEdgePlacement(0, { width: 12, height: 64 }, { x: 0, y: 0, width: 15, height: 900 }, [], 'right'), null);
  assert.equal(findMascotEdgePlacement(0, { width: 12, height: 64 }, { x: 0, y: 0, width: 390, height: 65 }, [], 'left'), null);
  assert.equal(findMascotEdgePlacement(NaN, { width: 12, height: 64 }, viewport, [], 'right'), null);
  assert.equal(findMascotRestingPlacement({ x: Infinity, y: 0 }, { width: 80, height: 94 }, viewport, [], 'left'), null);
});

test('placement helpers do not modify stored preferences, dimensions or obstacle lists', () => {
  const preferred = Object.freeze({ x: 1300, y: 500 }), size = Object.freeze({ width: 80, height: 94 });
  const obstacles = Object.freeze([Object.freeze({ x: 1100, y: 400, width: 300, height: 200 })]);
  const snapshot = JSON.stringify({ preferred, size, obstacles, viewport });
  findMascotRestingPlacement(preferred, size, viewport, obstacles, 'right');
  findMascotEdgePlacement(preferred.y, size, viewport, obstacles, 'right');
  assert.equal(JSON.stringify({ preferred, size, obstacles, viewport }), snapshot);
});

test('generated layouts keep returned bodies in their half and handles on their exact edge', () => {
  let seed = 27317;
  const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
  for (let i = 0; i < 100; i++) {
    const screen = { x: random() * 20, y: random() * 40, width: 340 + random() * 1100, height: 400 + random() * 500 };
    const obstacles = Array.from({ length: 12 }, () => ({ x: screen.x + random() * screen.width, y: screen.y + random() * screen.height, width: 20 + random() * 160, height: 20 + random() * 100 }));
    const side = i % 2 ? 'left' : 'right';
    for (const [kind, size] of [['body', { width: 80, height: 94 }], ['edge', { width: 18, height: 64 }]]) {
      const point = kind === 'body' ? findMascotRestingPlacement({ x: screen.x + screen.width - 100, y: screen.y + 200 }, size, screen, obstacles, side) : findMascotEdgePlacement(screen.y + 200, size, screen, obstacles, side);
      if (!point) continue;
      assert.ok(point.y >= screen.y && point.y + size.height <= screen.y + screen.height);
      if (kind === 'edge') assert.equal(point.x, side === 'left' ? screen.x + 2 : screen.x + screen.width - size.width - 2);
      else if (side === 'left') assert.ok(point.x + size.width <= screen.x + screen.width / 2);
      else assert.ok(point.x >= screen.x + screen.width / 2);
      for (const obstacle of obstacles) assert.equal(mascotRectsOverlap({ ...point, ...size }, obstacle, 4), false);
    }
  }
});
