import test from 'node:test';
import assert from 'node:assert/strict';
import { followMatrix, frameAt, layerVertices, visiblePoints } from '../scripts/layers.mjs';

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

test('a layer frames by the part of its texture that shows', () => {
  // A 10 x 10 quad whose texture shows only its middle half; v runs down in image space.
  const quad = { vertices: [0, 0, 10, 0, 0, 10, 10, 10], uvs: [0, 1, 1, 1, 0, 0, 1, 0], triangles: [0, 3, 1, 3, 0, 2] };
  const points = visiblePoints(quad, [0.25, 0.25, 0.75, 0.75]);
  const xs = points.filter((_, i) => i % 2 === 0), ys = points.filter((_, i) => i % 2 === 1);
  const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} vs ${b}`);
  close(Math.min(...xs), 2.5); close(Math.max(...xs), 7.5); close(Math.min(...ys), 2.5); close(Math.max(...ys), 7.5);
  // Only the top-left quarter shows (image space: small u, small v = top).
  const corner = visiblePoints(quad, [0, 0, 0.5, 0.5]);
  close(Math.max(...corner.filter((_, i) => i % 2 === 0)), 5);
  close(Math.min(...corner.filter((_, i) => i % 2 === 1)), 5);
  // A tiled texture (UVs past 1) counts whole; an animated UV map moves the window.
  assert.equal(visiblePoints({ ...quad, uvs: [0, 2, 2, 2, 0, 0, 2, 0] }, [0.25, 0.25, 0.75, 0.75]).length, 12);
  const shifted = visiblePoints(quad, [0.25, 0.25, 0.75, 0.75], [0.5, 0, 0.5, 0]);
  close(Math.max(...shifted.filter((_, i) => i % 2 === 0)), 10);
});

test('a tilted entry projects its 3D vertices with the frame\'s depth column', () => {
  // x' = a x + b y + e z + tx, y' = c x + d y + f z + ty: frame (after t) [a, b, c, d, tx, ty, ..., e, f] at 15 and 16.
  const f = [1, 0, 0, 1, 10, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0, 2, -3];
  assert.deepEqual(layerVertices({ vertices: [1, 2, 3], follow: null }, f, null, undefined, true), [1 + 6 + 10, 2 - 9]);
  // Under a bone follower the depth column turns with the follower.
  const bone = { worldX: 0, worldY: 0, scaleX: 1, scaleY: 1, getWorldRotationX: () => 90 };
  const follow = { bone: 'b', xy: true, rotation: true, localScale: false, mirrored: false, parent: [1, 0, 0, 1], position: [0, 0], angle: 0 };
  const [x, y] = layerVertices({ vertices: [0, 0, 1], follow }, [1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0, 1, 0], bone, undefined, true);
  assert.ok(Math.abs(x) < 1e-9 && Math.abs(y - 1) < 1e-9, `${x}, ${y}`);
  // Its visible part keeps the third coordinate.
  const tri = { vertices: [0, 0, 0, 10, 0, 5, 0, 10, -5], uvs: [0, 1, 1, 1, 0, 0], triangles: [0, 1, 2] };
  assert.equal(visiblePoints(tri, [0, 0, 1, 1], undefined, 3).length % 3, 0);
});
