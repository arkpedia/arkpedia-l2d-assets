import test from 'node:test';
import assert from 'node:assert/strict';
import { cp, mkdtemp, readFile, rm, writeFile, unlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { folderFor, sha256, skeletonVersion, slugFor, validateRepository, webpSize } from '../scripts/manifest.mjs';

const fixtures = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures');
const names = JSON.parse(await readFile(path.join(fixtures, 'names.json'), 'utf8'));
const header = (...strings) => Buffer.concat(strings.map((s) => Buffer.concat([Buffer.from([Buffer.byteLength(s) + 1]), Buffer.from(s)])));

test('slug and folder names follow the shared naming table', () => {
  for (const row of names) {
    assert.equal(slugFor(row.skinId), row.slug);
    assert.equal(folderFor(row.skinId, row.md5), row.folder);
  }
  for (const bad of ['', 'char_1/2#3', 'char_x@y', '../char#1', 'char x#1']) assert.throws(() => slugFor(bad));
  assert.throws(() => folderFor('char_1044_hsgma2#2', 'B1259EDB8FFF5A0A56F0B01FE29DC6D9'));
  assert.throws(() => folderFor('char_1044_hsgma2#2', 'b1259edb'));
});

test('skeleton versions come from the file: binary header or JSON skeleton.spine', () => {
  assert.equal(skeletonVersion(header('hash', '3.8.99')), '3.8.99');
  assert.equal(skeletonVersion(Buffer.from('{"skeleton":{"hash":"x","spine":"3.8.99"}}')), '3.8.99');
  assert.throws(() => skeletonVersion(header('hash', 'nope')));
  assert.throws(() => skeletonVersion(Buffer.from([0x80])));
});

test('WebP sizes are read from the lossless header; lossy WebP is refused', async () => {
  assert.deepEqual(webpSize(await readFile(path.join(fixtures, 'tiny', 'page0.webp'))), { width: 4, height: 4 });
  const lossy = Buffer.alloc(40);
  lossy.write('RIFF', 0); lossy.writeUInt32LE(32, 4); lossy.write('WEBP', 8); lossy.write('VP8 ', 12); lossy.writeUInt32LE(20, 16);
  assert.throws(() => webpSize(lossy), /lossy/);
  assert.throws(() => webpSize(Buffer.from('not a webp file at all, really not')), /Not a WebP/);
});

const record = (file, bytes, extra = {}) => ({ file, ...extra, bytes: bytes.length, sha256: sha256(bytes) });

/** A temporary repository holding one valid model built from test/fixtures/tiny. */
async function fixtureRepo() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'l2d-test-'));
  const skinId = 'char_9999_test@unit#1';
  const md5 = 'abcdef0123456789abcdef0123456789';
  const folder = folderFor(skinId, md5);
  await cp(path.join(fixtures, 'tiny'), path.join(root, folder), { recursive: true });
  const read = (name) => readFile(path.join(root, folder, name));
  const model = {
    schemaVersion: 1,
    skinId,
    dynIllustId: 'dyn_illust_char_9999_test_unit#1',
    spineVersion: '3.8.99',
    skeleton: record('skeleton.json', await read('skeleton.json'), { format: 'json' }),
    atlas: record('skeleton.atlas', await read('skeleton.atlas')),
    textures: [record('page0.webp', await read('page0.webp'), { width: 4, height: 4 })],
    premultipliedAlpha: true,
    animations: { Idle: 1.235, Interact: 0.5 },
    bounds: { x: -50, y: 0, width: 100, height: 200 },
    mixes: [{ from: 'Idle', to: 'Interact', duration: 0.5 }],
    source: { server: 'en', bundle: 'arts/dynchars/char_9999_test_unit#1.ab', md5, resVersion: 'test' },
  };
  const manifest = { schemaVersion: 1, server: 'en', resVersion: 'test', models: { [skinId]: `${folder}/model.json` } };
  const save = async (m = model, man = manifest) => {
    await writeFile(path.join(root, folder, 'model.json'), JSON.stringify(m, null, 2));
    await writeFile(path.join(root, 'manifest.json'), JSON.stringify(man, null, 2));
  };
  await save();
  return { root, folder, model, manifest, save, skinId };
}

test('a complete model validates, including the Spine re-read', async () => {
  const repo = await fixtureRepo();
  try {
    assert.deepEqual(await validateRepository(repo.root), { listed: 1, folders: 1, failures: 0 });
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('a broken hash, size or missing file fails validation', async () => {
  const repo = await fixtureRepo();
  try {
    const page = path.join(repo.root, repo.folder, 'page0.webp');
    const original = await readFile(page);
    const altered = Buffer.from(original); altered[altered.length - 1] ^= 1;
    await writeFile(page, altered);
    await assert.rejects(validateRepository(repo.root), /sha256 does not match/);
    await writeFile(page, Buffer.concat([original, Buffer.from([0])]));
    await assert.rejects(validateRepository(repo.root), /bytes/);
    await unlink(page);
    await assert.rejects(validateRepository(repo.root), /missing file page0\.webp/);
    await writeFile(page, original);
    await validateRepository(repo.root);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('missing or wrong fields fail validation', async () => {
  const repo = await fixtureRepo();
  const variants = [
    [(m) => { delete m.bounds; }, /bounds/],
    [(m) => { delete m.animations; }, /animations/],
    [(m) => { m.premultipliedAlpha = false; }, /premultipliedAlpha/],
    [(m) => { delete m.source.md5; }, /source/],
    [(m) => { m.source.resVersion = ''; }, /source/],
    [(m) => { m.spineVersion = '4.1.0'; }, /Spine version/],
    [(m) => { m.skeleton.format = 'binary'; }, /skeleton/],
    [(m) => { m.textures[0].width = 8; }, /4x4/],
    [(m) => { m.textures[0].file = 'atlas.png'; }, /page0\.webp/],
    [(m) => { m.animations = { Idle: 9 }; }, /animations differ/],
    // An entrance skeleton picked by mistake has only Start: refused before any re-read.
    [(m) => { m.animations = { Start: 14.667 }; }, /animations must include Idle \(has Start\)/],
    [(m) => { m.animations = { Interact: 0.5, idle: 1.235 }; }, /must include Idle/],
    [(m) => { m.bounds = { x: 0, y: 0, width: 100, height: 200 }; }, /bounds differ/],
    [(m) => { m.mixes = [{ from: 'Idle' }]; }, /mixes/],
    [(m) => { m.source.md5 = '0'.repeat(32); }, /folder must be/],
    [(m) => { m.skinId = 'char_9999_test@other#1'; }, /folder must be/],
  ];
  try {
    for (const [mutate, message] of variants) {
      const model = structuredClone(repo.model);
      mutate(model);
      await repo.save(model);
      await assert.rejects(validateRepository(repo.root), message, `expected ${message}`);
    }
    await repo.save();
    await validateRepository(repo.root);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('the manifest must point at existing folders of the same skin', async () => {
  const repo = await fixtureRepo();
  try {
    await repo.save(repo.model, { ...repo.manifest, models: { [repo.skinId]: 'models/char_9999_test_unit_1/000000000000/model.json' } });
    await assert.rejects(validateRepository(repo.root), /does not exist/);
    await repo.save(repo.model, { ...repo.manifest, models: { 'char_9999_test@unit#2': repo.manifest.models[repo.skinId] } });
    await assert.rejects(validateRepository(repo.root), /belongs to/);
    await repo.save(repo.model, { ...repo.manifest, models: { [repo.skinId]: '../outside/model.json' } });
    await assert.rejects(validateRepository(repo.root), /bad manifest path/);
    await repo.save(repo.model, { ...repo.manifest, schemaVersion: 2 });
    await assert.rejects(validateRepository(repo.root), /schemaVersion/);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('atlas pages must match the texture list, and folders hold nothing else', async () => {
  const repo = await fixtureRepo();
  try {
    const atlas = path.join(repo.root, repo.folder, 'skeleton.atlas');
    const text = (await readFile(atlas, 'utf8')).replace('page0.webp', 'dyn_illust_char_9999_test#1.png');
    await writeFile(atlas, text);
    const model = structuredClone(repo.model);
    model.atlas = record('skeleton.atlas', Buffer.from(text));
    await repo.save(model);
    await assert.rejects(validateRepository(repo.root), /atlas pages/);
    await repo.save();
    await writeFile(atlas, await readFile(path.join(fixtures, 'tiny', 'skeleton.atlas')));
    await writeFile(path.join(repo.root, repo.folder, 'extra.png'), 'x');
    await assert.rejects(validateRepository(repo.root), /unexpected files extra\.png/);
    await unlink(path.join(repo.root, repo.folder, 'extra.png'));
    const packed = (await readFile(atlas, 'utf8')).replace('size: 4,4', 'size: 8,8');
    await writeFile(atlas, packed);
    const resized = structuredClone(repo.model);
    resized.atlas = record('skeleton.atlas', Buffer.from(packed));
    await repo.save(resized);
    await assert.rejects(validateRepository(repo.root), /packed at 8x8/);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('sync-failures.json, when present, must record md5, code, resVersion and error per skin', async () => {
  const repo = await fixtureRepo();
  const file = path.join(repo.root, 'sync-failures.json');
  const entry = { md5: 'a'.repeat(32), code: 'abcdef012345', resVersion: 'test', error: 'SyncError: x' };
  try {
    await writeFile(file, JSON.stringify({ schemaVersion: 1, failures: {} }));
    assert.equal((await validateRepository(repo.root)).failures, 0);
    await writeFile(file, JSON.stringify({ schemaVersion: 1, failures: { 'char_003_kalts@boc#6': entry } }));
    assert.equal((await validateRepository(repo.root)).failures, 1);
    const broken = [
      [{ schemaVersion: 2, failures: {} }, /schemaVersion: 1/],
      [{ schemaVersion: 1, failures: { 'char_003_kalts@boc#6': { ...entry, md5: 'x' } } }, /must give md5/],
      [{ schemaVersion: 1, failures: { 'char_003_kalts@boc#6': { ...entry, code: 'short' } } }, /must give md5/],
      [{ schemaVersion: 1, failures: { '../escape#1': entry } }, /Unexpected skinId/],
    ];
    for (const [document, message] of broken) {
      await writeFile(file, JSON.stringify(document));
      await assert.rejects(validateRepository(repo.root), message, `expected ${message}`);
    }
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});
