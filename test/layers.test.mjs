import test from 'node:test';
import assert from 'node:assert/strict';
import { followMatrix, frameAt, layerVertices } from '../scripts/layers.mjs';

const frame = (t, values = {}) => {
  const f = [t, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0];
  for (const [i, v] of Object.entries(values)) f[Number(i)] = v;
  return f;
};

test('a timeline interpolates linearly, steps its active flag, loops from loopFrom or holds', () => {
  const timeline = { length: 2, loop: true, loopFrom: 0, frames: [frame(0, { 5: 0, 11: 1 }), frame(1, { 5: 10, 11: 0 }), frame(2, { 5: 0, 11: 1 })] };
  assert.equal(frameAt(timeline, 0.5)[4], 5);
  assert.equal(frameAt(timeline, 0.5)[10], 1, 'active holds until the next frame');
  assert.equal(frameAt(timeline, 1)[10], 0);
  assert.equal(frameAt(timeline, 2.5)[4], 5, 'wraps to the start');
  // A prelude played once, then the loop from 1 s.
  const prelude = { ...timeline, loopFrom: 1 };
  assert.equal(frameAt(prelude, 2.5)[4], 5);
  assert.equal(frameAt(prelude, 3.25)[4], 7.5);
  const once = { ...timeline, loop: false };
  assert.equal(frameAt(once, 99)[4], 0);
  assert.deepEqual(frameAt({ length: 0, loop: false, loopFrom: 0, frames: [frame(0)] }, 5), frame(0).slice(1));
});

test('a bone follower takes the bone position, rotation (mirrored, flipped) and scale', () => {
  const bone = { worldX: 10, worldY: 20, scaleX: 2, scaleY: 3, getWorldRotationX: () => 90 };
  const base = { bone: 'b', xy: true, rotation: true, localScale: false, mirrored: false, parent: [100, 0, 0, 100], position: [5, 6], angle: 0 };
  const close = (a, b) => a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < 1e-9, `${a} vs ${b}`));
  close(followMatrix(base, bone), [0, -100, 100, 0, 10, 20]);
  close(followMatrix({ ...base, mirrored: true }, bone), [0, 100, -100, 0, 10, 20]);
  close(followMatrix({ ...base, xy: false, rotation: false, angle: 0 }, bone), [100, 0, 0, 100, 5, 6]);
  close(followMatrix({ ...base, rotation: false, localScale: true }, bone), [200, 0, 0, 300, 10, 20]);
  // spine-unity turns a follower half round when the bone's scaleX is negative.
  close(followMatrix({ ...base, localScale: true }, { ...bone, scaleX: -1, scaleY: 1, getWorldRotationX: () => 0 }), [100, 0, 0, -100, 10, 20]);
  const layer = { vertices: [1, 0, 0, 1], follow: { ...base, rotation: false } };
  close(layerVertices(layer, frame(0, { 5: 1 }).slice(1), bone), [210, 20, 110, 120]);
  close(layerVertices({ vertices: [1, 2], follow: null }, null, null), [1, 2]);
});
