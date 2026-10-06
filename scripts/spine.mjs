// Headless use of the vendored spine-core 3.8 runtime (see vendor/spine-core-3.8/README.md).
// The vendored file is a global script; it is evaluated as is, never edited.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

export const RUNTIME_PATH = fileURLToPath(new URL('../vendor/spine-core-3.8/spine-core.js', import.meta.url));

let cached;

/** Returns the `spine` namespace of the vendored 3.8 runtime. */
export function loadSpine() {
  if (!cached) {
    const source = readFileSync(RUNTIME_PATH, 'utf8');
    vm.runInThisContext(`${source}\n;globalThis.__arkpediaSpine38 = spine;`, { filename: RUNTIME_PATH });
    cached = globalThis.__arkpediaSpine38;
    delete globalThis.__arkpediaSpine38;
  }
  return cached;
}

/** A JSON skeleton starts with `{` (after an optional BOM and whitespace); anything else is binary. */
export function isJsonSkeleton(bytes) {
  let i = 0;
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) i = 3;
  while (i < bytes.length && (bytes[i] === 0x20 || bytes[i] === 0x09 || bytes[i] === 0x0a || bytes[i] === 0x0d)) i++;
  return bytes[i] === 0x7b;
}

const round3 = (value) => Math.round(value * 1000) / 1000;

/** Parses an atlas with the real runtime and a dummy texture loader. */
export function readAtlas(atlasText) {
  const spine = loadSpine();
  return new spine.TextureAtlas(atlasText, () => new spine.FakeTexture({ width: 1, height: 1 }));
}

/**
 * Reads a skeleton with the 3.8 runtime and reports what the site needs:
 * the version, every animation's duration (seconds, 3 decimals) and the bounds of
 * the setup pose with Idle applied at time 0 (setup pose alone when there is no Idle).
 * Throws when the skeleton references a region the atlas does not have.
 */
export function inspectSkeleton(skeletonBytes, atlasText) {
  const spine = loadSpine();
  const atlas = readAtlas(atlasText);
  const loader = new spine.AtlasAttachmentLoader(atlas);
  const json = isJsonSkeleton(skeletonBytes);
  const data = json
    ? new spine.SkeletonJson(loader).readSkeletonData(Buffer.from(skeletonBytes).toString('utf8').replace(/^﻿/, ''))
    : new spine.SkeletonBinary(loader).readSkeletonData(new Uint8Array(skeletonBytes));
  if (!data.animations.length) throw new Error('Skeleton has no animations');
  const animations = {};
  for (const animation of data.animations) animations[animation.name] = round3(animation.duration);

  const skeleton = new spine.Skeleton(data);
  skeleton.setToSetupPose();
  skeleton.updateWorldTransform();
  if (data.findAnimation('Idle')) {
    const state = new spine.AnimationState(new spine.AnimationStateData(data));
    state.setAnimation(0, 'Idle', true);
    state.update(0);
    state.apply(skeleton);
    skeleton.updateWorldTransform();
  }
  const offset = new spine.Vector2();
  const size = new spine.Vector2();
  skeleton.getBounds(offset, size, []);
  const bounds = { x: Math.round(offset.x), y: Math.round(offset.y), width: Math.round(size.x), height: Math.round(size.y) };
  if (!Object.values(bounds).every(Number.isFinite) || bounds.width <= 0 || bounds.height <= 0) {
    throw new Error(`Skeleton has no visible attachments in its first pose: ${JSON.stringify(bounds)}`);
  }
  return {
    spineVersion: data.version,
    format: json ? 'json' : 'binary',
    animations,
    bounds,
    pages: atlas.pages.map((page) => page.name),
    regions: atlas.regions.length,
  };
}
