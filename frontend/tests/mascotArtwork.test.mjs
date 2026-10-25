import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/mascot/mascotArtwork.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { MASCOT_ATLAS_POSES, mascotArtworkPose, mascotAtlasFrame, resolveMascotArtwork } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const atlas = { src: '/study.png', columns: 4, rows: 2, cellWidth: 384, cellHeight: 512, poses: MASCOT_ATLAS_POSES };
const assets = { idle: '/idle.png', fallback: '/fallback.png', pixelated: false, atlas };
const gesture = kind => ({ id: 1, kind, until: 1000 });
const resolve = patch => resolveMascotArtwork({ assets, state: 'idle', gesture: null, interactionEnabled: true,
  dragging: false, reducedMotion: false, failedSources: [], fallbackSource: '/bundled.png', ...patch });

test('eight states select complete, non-overlapping cells and reach the final row correctly', () => {
  const frames = Object.keys(MASCOT_ATLAS_POSES).map(pose => mascotAtlasFrame(atlas, pose));
  assert.equal(new Set(frames.map(frame => `${frame.column}:${frame.row}`)).size, 8);
  for (const frame of frames) assert.equal(frame.aspectRatio, '384 / 512');
  assert.deepEqual(frames[0].style, { width: '400%', height: '200%', left: '0%', top: '0%' });
  assert.deepEqual(frames[7].style, { width: '400%', height: '200%', left: '-300%', top: '-100%' });
});

test('invalid dimensions or out-of-range cell indices cannot expose adjacent sheet content', () => {
  for (const value of [0, -1, 1.5, NaN, Infinity, 17]) {
    assert.equal(mascotAtlasFrame({ ...atlas, columns: value }, 'idle'), null);
  }
  for (const value of [-1, 8, 1.5, NaN, Infinity]) {
    assert.equal(mascotAtlasFrame({ ...atlas, poses: { ...atlas.poses, idle: value } }, 'idle'), null);
  }
  assert.equal(mascotAtlasFrame({ ...atlas, cellHeight: 0 }, 'idle'), null);
  assert.equal(mascotAtlasFrame({ ...atlas, src: '' }, 'idle'), null);
});

test('source-pixel corrections keep complete explaining hair and exclude its overlap from thinking', () => {
  const adjusted = { ...atlas, poseAdjustments: { thinking: { clipRight: 12 }, explaining: { sourceOffsetX: -12 } } };
  const thinking = mascotAtlasFrame(adjusted, 'thinking');
  assert.equal(thinking.style.left, '-200%');
  assert.equal(thinking.clipPath, 'inset(0 3.125% 0 0)');
  const explaining = mascotAtlasFrame(adjusted, 'explaining');
  assert.equal(explaining.style.left, '-296.875%');
  assert.equal(explaining.aspectRatio, '384 / 512');
  for (const adjustment of [{ sourceOffsetX: NaN }, { sourceOffsetX: -1 }, { clipRight: -1 }, { clipRight: 384 }]) {
    assert.equal(mascotAtlasFrame({ ...atlas, poseAdjustments: { idle: adjustment } }, 'idle'), null);
  }
});

test('learning states retain their expression while a low priority gesture is finishing', () => {
  for (const state of ['thinking', 'explaining', 'searching', 'success', 'uncertain', 'error']) {
    for (const kind of ['nod', 'tilt', 'hop', 'stretch', 'pet']) assert.equal(mascotArtworkPose(state, gesture(kind), true, false), state);
  }
  assert.equal(mascotArtworkPose('idle', gesture('tilt'), true, false), 'uncertain');
  assert.equal(mascotArtworkPose('reading', gesture('pet'), true, false), 'success');
  assert.equal(mascotArtworkPose('idle', gesture('hop'), false, false), 'idle');
  assert.equal(mascotArtworkPose('reading', gesture('hop'), true, true), 'reading');
});

test('new artwork uses a static atlas for pet and reduced motion instead of loading a legacy GIF', () => {
  for (const reducedMotion of [false, true]) {
    const result = resolve({ assets: { ...assets, pet: '/old.gif' }, gesture: gesture('pet'), reducedMotion });
    assert.equal(result.source, '/study.png'); assert.equal(result.asset, 'atlas');
    assert.equal(result.pose, 'success'); assert.equal(result.pixelated, false);
  }
});

test('a failed atlas is skipped for every later state, preserving local and final fallback layers', () => {
  const failedSources = ['/study.png'];
  for (const state of Object.keys(MASCOT_ATLAS_POSES)) {
    const result = resolve({ state, failedSources });
    assert.equal(result.source, '/idle.png'); assert.equal(result.frame, null);
  }
  assert.equal(resolve({ failedSources: ['/study.png', '/idle.png'] }).source, '/fallback.png');
  const result = resolve({ failedSources: ['/study.png', '/idle.png', '/fallback.png'] });
  assert.equal(result.source, '/bundled.png'); assert.equal(result.asset, 'fallback'); assert.equal(result.pixelated, true);
  assert.deepEqual(failedSources, ['/study.png']);
});

test('legacy individual portraits and optional GIFs remain compatible with reduced-motion settings', () => {
  const legacy = { idle: '/portrait.png', thinking: '/thinking.png', happy: '/happy.png', error: '/error.png', pet: '/pet.gif', pixelated: false };
  assert.equal(resolve({ assets: legacy, state: 'thinking' }).source, '/thinking.png');
  assert.equal(resolve({ assets: legacy, state: 'explaining' }).source, '/happy.png');
  assert.equal(resolve({ assets: legacy, state: 'error' }).source, '/error.png');
  assert.equal(resolve({ assets: legacy, gesture: gesture('pet') }).asset, 'pet');
  assert.equal(resolve({ assets: legacy, gesture: gesture('pet'), reducedMotion: true }).source, '/portrait.png');
  assert.equal(resolve({ assets: legacy, gesture: gesture('pet'), dragging: true }).source, '/portrait.png');
});
