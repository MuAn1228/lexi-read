import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const compile = text => ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const url = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const position = url(compile(await readFile(new URL('../src/mascot/mascotPosition.ts', import.meta.url), 'utf8')));
const source = compile(await readFile(new URL('../src/mascot/mascotBubblePosition.ts', import.meta.url), 'utf8')).replace("'./mascotPosition'", JSON.stringify(position));
const { findMascotBubblePlacement } = await import(url(source));
const { mascotRectsOverlap } = await import(position);
const options = { character: { x: 350, y: 300 }, characterSize: { width: 80, height: 94 }, bubbleSize: { width: 220, height: 64 }, viewport: { x: 0, y: 0, width: 800, height: 700 }, obstacles: [] };

function valid(result, input = options) {
  assert.ok(result);
  const character = { ...result.character, ...input.characterSize }, bubble = { ...result.bubble, ...input.bubbleSize };
  const gap = input.gap ?? 10, inset = input.inset ?? 12;
  assert.equal(mascotRectsOverlap(character, bubble), false);
  for (const [rect, obstacles] of [[character, input.characterObstacles ?? input.obstacles], [bubble, input.obstacles]]) {
    assert.ok(rect.x >= input.viewport.x + inset - .01 && rect.y >= input.viewport.y + inset - .01);
    assert.ok(rect.x + rect.width <= input.viewport.x + input.viewport.width - inset + .01 && rect.y + rect.height <= input.viewport.y + input.viewport.height - inset + .01);
    for (const obstacle of obstacles) assert.equal(mascotRectsOverlap(rect, obstacle, 4), false);
  }
  if (result.side === 'top') assert.equal(character.y - bubble.y - bubble.height, gap);
  if (result.side === 'bottom') assert.equal(bubble.y - character.y - character.height, gap);
  if (result.side === 'left') assert.equal(character.x - bubble.x - bubble.width, gap);
  if (result.side === 'right') assert.equal(bubble.x - character.x - character.width, gap);
  const horizontal = result.side === 'top' || result.side === 'bottom';
  const target = (horizontal ? bubble.x : bubble.y) + result.tailOffset;
  const start = horizontal ? character.x : character.y;
  assert.ok(target >= start - .01 && target <= start + (horizontal ? character.width : character.height) + .01);
  return result;
}

test('an unobstructed bubble remains attached above the unchanged character', () => {
  const result = valid(findMascotBubblePlacement(options));
  assert.equal(result.side, 'top'); assert.deepEqual(result.character, options.character);
  assert.equal(result.tailOffset, 110);
});

test('each side is selected when only its adjacent rectangle is free', () => {
  const blocks = { top: { x: 180, y: 215, width: 450, height: 75 }, bottom: { x: 180, y: 404, width: 450, height: 80 }, left: { x: 110, y: 298, width: 230, height: 96 }, right: { x: 440, y: 298, width: 240, height: 96 } };
  for (const side of ['top', 'bottom', 'left', 'right']) {
    const input = { ...options, obstacles: Object.entries(blocks).filter(([key]) => key !== side).map(([, value]) => value) };
    const result = valid(findMascotBubblePlacement(input), input);
    assert.equal(result.side, side); assert.deepEqual(result.character, input.character);
  }
});

test('visual viewport offsets and corner clamping still aim the tail at the character', () => {
  const input = { ...options, character: { x: 36, y: 180 }, viewport: { x: 14, y: 38, width: 390, height: 550 } };
  const result = valid(findMascotBubblePlacement(input), input);
  assert.deepEqual(result.character, input.character); assert.equal(result.bubble.x, 26);
  assert.equal(result.bubble.x + result.tailOffset, 76);
});

test('the bubble slides within a narrow adjacent slot without relocating the character', () => {
  const input = { ...options, obstacles: [
    { x: 180, y: 215, width: 450, height: 75 },
    { x: 350, y: 404, width: 280, height: 80 },
    { x: 440, y: 298, width: 240, height: 96 },
    { x: 120, y: 280, width: 220, height: 20 },
    { x: 120, y: 377, width: 220, height: 30 },
  ] };
  const result = valid(findMascotBubblePlacement(input), input);
  assert.deepEqual(result.character, input.character);
  assert.equal(result.side, 'left');
  assert.equal(result.bubble.y, 309);
});

test('joint relocation finds room for the attached pair without mutating saved preferences', () => {
  const input = { ...options, character: { x: 350, y: 300 }, obstacles: [{ x: 230, y: 200, width: 360, height: 90 }, { x: 230, y: 404, width: 360, height: 90 }, { x: 110, y: 298, width: 230, height: 96 }, { x: 440, y: 298, width: 240, height: 96 }] };
  const before = structuredClone(input);
  const result = valid(findMascotBubblePlacement(input), input);
  assert.notDeepEqual(result.character, input.character); assert.deepEqual(input, before);
});

test('fixed dialogue rejects relocation even when the pair fits elsewhere in the viewport', () => {
  const input = { ...options, fixedCharacter: true, obstacles: [{ x: 230, y: 200, width: 360, height: 90 }, { x: 230, y: 404, width: 360, height: 90 }, { x: 110, y: 298, width: 230, height: 96 }, { x: 440, y: 298, width: 240, height: 96 }] };
  const before = structuredClone(input);
  assert.equal(findMascotBubblePlacement(input), null);
  const movable = valid(findMascotBubblePlacement({ ...input, fixedCharacter: false }), input);
  assert.notDeepEqual(movable.character, input.character);
  assert.deepEqual(input, before);
});

test('a parked character may overlap a soft reading surface while its bubble remains clear', () => {
  const input = { ...options, fixedCharacter: true, characterObstacles: [], obstacles: [{ x: 340, y: 290, width: 100, height: 110 }] };
  const before = structuredClone(input);
  const result = valid(findMascotBubblePlacement(input), input);
  assert.deepEqual(result.character, input.character);
  assert.equal(result.side, 'bottom');
  assert.equal(mascotRectsOverlap({ ...result.character, ...input.characterSize }, input.obstacles[0]), true);
  assert.equal(findMascotBubblePlacement({ ...input, characterObstacles: undefined }), null);
  assert.deepEqual(input, before);
});

test('fixed dialogue still protects navigation under the character and content under the bubble', () => {
  const navigation = { x: 340, y: 290, width: 100, height: 110 };
  assert.equal(findMascotBubblePlacement({ ...options, fixedCharacter: true, characterObstacles: [navigation] }), null);
  const bubbleBlocked = { ...options, fixedCharacter: true, characterObstacles: [], obstacles: [{ x: 0, y: 0, width: 800, height: 700 }] };
  assert.equal(findMascotBubblePlacement(bubbleBlocked), null);
});

test('fixed dialogue chooses any clear adjacent side without changing character size or position', () => {
  const blocks = { top: { x: 180, y: 215, width: 450, height: 75 }, bottom: { x: 180, y: 404, width: 450, height: 80 }, left: { x: 110, y: 298, width: 230, height: 96 }, right: { x: 440, y: 298, width: 240, height: 96 } };
  for (const side of ['top', 'bottom', 'left', 'right']) {
    const input = { ...options, fixedCharacter: true, obstacles: Object.entries(blocks).filter(([key]) => key !== side).map(([, value]) => value) };
    const before = structuredClone(input);
    const result = valid(findMascotBubblePlacement(input), input);
    assert.equal(result.side, side);
    assert.deepEqual(result.character, input.character);
    assert.deepEqual(input, before);
  }
});

test('crowded and undersized viewports reject impossible pairs instead of separating or clipping them', () => {
  assert.equal(findMascotBubblePlacement({ ...options, obstacles: [{ x: 0, y: 0, width: 800, height: 700 }] }), null);
  assert.equal(findMascotBubblePlacement({ ...options, viewport: { x: 0, y: 0, width: 100, height: 120 } }), null);
  assert.equal(findMascotBubblePlacement({ ...options, bubbleSize: { width: NaN, height: 64 } }), null);
});

test('measured tall bubbles and a narrow character use the real size and a valid tail', () => {
  for (const characterSize of [{ width: 12, height: 64 }, { width: 64, height: 76 }]) {
    const input = { ...options, characterSize, bubbleSize: { width: 148, height: 122 }, viewport: { x: 0, y: 0, width: 390, height: 500 }, character: { x: 350, y: 320 }, inset: 8 };
    valid(findMascotBubblePlacement(input), input);
  }
});

test('deterministic varied layouts never return detached tails, clipping, or protected-content overlap', () => {
  let seed = 17191;
  const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
  let count = 0;
  for (let i = 0; i < 100; i++) {
    const input = { ...options, viewport: { x: random() * 14, y: random() * 30, width: 390 + random() * 800, height: 400 + random() * 400 }, character: { x: random() * 500, y: random() * 500 }, bubbleSize: { width: 120 + random() * 160, height: 50 + random() * 80 }, obstacles: Array.from({ length: 6 }, () => ({ x: random() * 700, y: random() * 500, width: 30 + random() * 170, height: 20 + random() * 70 })) };
    const result = findMascotBubblePlacement(input);
    if (result) { valid(result, input); count++; }
  }
  assert.ok(count > 25, 'generated coverage must include successful placements');
});
