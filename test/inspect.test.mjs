import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { atlasPageSizes, sha256 } from '../scripts/manifest.mjs';
import { RUNTIME_PATH, inspectSkeleton, readAtlas } from '../scripts/spine.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const fixtures = path.join(here, 'fixtures');

test('the vendored Spine 3.8 runtime is the unmodified upstream file', async () => {
  const readme = await readFile(path.join(root, 'vendor', 'spine-core-3.8', 'README.md'), 'utf8');
  for (const file of ['spine-core.js', 'LICENSE']) {
    const bytes = await readFile(path.join(root, 'vendor', 'spine-core-3.8', file));
    const row = new RegExp(`\\| \`${file.replace('.', '\\.')}\` \\| (\\d+) \\| \`([a-f0-9]{64})\` \\|`).exec(readme);
    assert.ok(row, `README lists ${file}`);
    assert.equal(bytes.length, Number(row[1]));
    assert.equal(sha256(bytes), row[2]);
  }
  assert.equal(RUNTIME_PATH, path.join(root, 'vendor', 'spine-core-3.8', 'spine-core.js'));
});

test('the inspector reads a small real skeleton: durations to 3 decimals, bounds of the first Idle pose', async () => {
  const result = inspectSkeleton(
    await readFile(path.join(fixtures, 'tiny', 'skeleton.json')),
    await readFile(path.join(fixtures, 'tiny', 'skeleton.atlas'), 'utf8'),
  );
  assert.equal(result.spineVersion, '3.8.99');
  assert.equal(result.format, 'json');
  assert.deepEqual(result.animations, { Idle: 1.235, Interact: 0.5 });
  assert.deepEqual(result.bounds, { x: -50, y: 0, width: 100, height: 200 });
  assert.deepEqual(result.pages, ['page0.webp']);
});

test('the inspector refuses a skeleton whose regions the atlas lacks', async () => {
  const skeleton = await readFile(path.join(fixtures, 'tiny', 'skeleton.json'));
  const atlas = (await readFile(path.join(fixtures, 'tiny', 'skeleton.atlas'), 'utf8')).replace('\nbody\n', '\nother\n');
  assert.throws(() => inspectSkeleton(skeleton, atlas), /Region not found/);
});

test('a rewritten two-page atlas (nian-shaped) reads as page0.webp, page1.webp with every region kept', async () => {
  const original = readAtlas(await readFile(path.join(fixtures, 'two-page.atlas'), 'utf8'));
  const rewritten = readAtlas(await readFile(path.join(fixtures, 'two-page.rewritten.atlas'), 'utf8'));
  assert.deepEqual(original.pages.map((p) => p.name), ['dyn_illust_char_2014_nian.png', 'dyn_illust_char_2014_nian2.png']);
  assert.deepEqual(rewritten.pages.map((p) => p.name), ['page0.webp', 'page1.webp']);
  assert.equal(rewritten.regions.length, original.regions.length);
  assert.deepEqual(rewritten.regions.map((r) => [r.name, r.page.name]), [
    ['A/H_Ahoge', 'page0.webp'], ['A/H_Hair.png', 'page0.webp'], ['B/D_Body', 'page1.webp'],
  ]);
  assert.deepEqual(atlasPageSizes(await readFile(path.join(fixtures, 'two-page.rewritten.atlas'), 'utf8')), [
    { width: 2048, height: 2048 }, { width: 2048, height: 1024 },
  ]);
});

const manifestPath = path.join(root, 'manifest.json');
const manifest = existsSync(manifestPath) ? JSON.parse(await readFile(manifestPath, 'utf8')) : { models: {} };

test('committed models re-read to their recorded animations and bounds', { skip: !Object.keys(manifest.models).length && 'no models yet' }, async () => {
  for (const [skinId, target] of Object.entries(manifest.models)) {
    const folder = path.join(root, path.dirname(target));
    const model = JSON.parse(await readFile(path.join(folder, 'model.json'), 'utf8'));
    const result = inspectSkeleton(await readFile(path.join(folder, model.skeleton.file)), await readFile(path.join(folder, 'skeleton.atlas'), 'utf8'));
    assert.deepEqual(result.animations, model.animations, skinId);
    assert.deepEqual(result.bounds, model.bounds, skinId);
    assert.equal(result.spineVersion, model.spineVersion, skinId);
  }
});

test('Hoshiguma the Breacher (Elite 2) matches the prototype that rendered it', { skip: !manifest.models['char_1044_hsgma2#2'] && 'model not synced' }, async () => {
  const folder = path.join(root, path.dirname(manifest.models['char_1044_hsgma2#2']));
  const result = inspectSkeleton(await readFile(path.join(folder, 'skeleton.skel')), await readFile(path.join(folder, 'skeleton.atlas'), 'utf8'));
  assert.deepEqual(result.animations, { Idle: 9.333, Interact: 13.6, Special: 15 });
  assert.deepEqual(result.bounds, { x: -1018, y: -218, width: 1976, height: 2027 });
});
