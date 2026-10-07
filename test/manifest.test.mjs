import test from 'node:test';
import assert from 'node:assert/strict';
import { cp, mkdtemp, readFile, rm, writeFile, unlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { folderFor, isMp3, sha256, skeletonVersion, slugFor, validateRepository, webpSize } from '../scripts/manifest.mjs';
import { inspectLayers, inspectSkeleton } from '../scripts/spine.mjs';

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
    dynEntranceId: null,
    entrance: null,
    layers: null,
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
    // layers is always written (null only for a bundle without an illustration prefab).
    [(m) => { delete m.layers; }, /layers must be present/],
    [(m) => { m.layers = { file: 'layers.json', bytes: 1, sha256: '0'.repeat(64) }; }, /missing file layers\.json/],
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

test('a model from the CN client validates; another server, or a bad resVersions, does not', async () => {
  const repo = await fixtureRepo();
  try {
    const cn = { ...repo.model, source: { ...repo.model.source, server: 'cn' } };
    await repo.save(cn, { ...repo.manifest, resVersions: { en: 'test', cn: 'cn-test' } });
    assert.deepEqual(await validateRepository(repo.root), { listed: 1, folders: 1, failures: 0 });
    await repo.save({ ...repo.model, source: { ...repo.model.source, server: 'jp' } });
    await assert.rejects(validateRepository(repo.root), /source must give server/);
    await repo.save(repo.model, { ...repo.manifest, resVersions: { en: 'test', jp: 'x' } });
    await assert.rejects(validateRepository(repo.root), /resVersions/);
    await repo.save(repo.model, { ...repo.manifest, resVersions: { en: '' } });
    await assert.rejects(validateRepository(repo.root), /resVersions/);
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

/** The fixture model with an entrance: the tiny skeleton again, its one animation named Start
 *  as an entrance's is, its own atlas page, and a soundtrack. */
async function entranceRepo() {
  const repo = await fixtureRepo();
  const dir = path.join(repo.root, repo.folder);
  const skeleton = JSON.parse(await readFile(path.join(fixtures, 'tiny', 'skeleton.json'), 'utf8'));
  skeleton.animations = { Start: skeleton.animations.Idle };
  const skeletonBytes = Buffer.from(JSON.stringify(skeleton));
  const atlasText = (await readFile(path.join(fixtures, 'tiny', 'skeleton.atlas'), 'utf8')).replace('page0.webp', 'entrance-page0.webp');
  const page = await readFile(path.join(fixtures, 'tiny', 'page0.webp'));
  const mp3 = Buffer.concat([Buffer.from([0xff, 0xfb, 0x90, 0x64]), Buffer.alloc(413)]);
  await writeFile(path.join(dir, 'entrance.json'), skeletonBytes);
  await writeFile(path.join(dir, 'entrance.atlas'), atlasText);
  await writeFile(path.join(dir, 'entrance-page0.webp'), page);
  await writeFile(path.join(dir, 'entrance.mp3'), mp3);
  const found = inspectSkeleton(skeletonBytes, atlasText);
  const model = structuredClone(repo.model);
  model.dynEntranceId = 'dyn_entrance_char_9999_test_unit#1';
  model.entrance = {
    skeleton: record('entrance.json', skeletonBytes, { format: 'json' }),
    atlas: record('entrance.atlas', Buffer.from(atlasText)),
    textures: [record('entrance-page0.webp', page, { width: 4, height: 4 })],
    animations: found.animations,
    bounds: found.bounds,
    camera: {
      frames: [[0, 11, 1170.1, 708.5, 0], [found.animations.Start, -36.4, 803.8, 818.7, -29.6]],
      fades: [{ color: [0, 0, 0], keys: [[0, 0], [0.5, 1], [found.animations.Start, 0]] }],
      handover: [1, 1, 1],
    },
    audio: record('entrance.mp3', mp3, { duration: 22.772 }),
  };
  await repo.save(model);
  return { ...repo, model, dir };
}

test('an entrance validates with its own skeleton, page and soundtrack', async () => {
  const repo = await entranceRepo();
  try {
    assert.deepEqual(Object.keys(repo.model.entrance.animations), ['Start']);
    assert.deepEqual(await validateRepository(repo.root), { listed: 1, folders: 1, failures: 0 });
    const still = structuredClone(repo.model);
    still.entrance.camera = null;
    await repo.save(still);
    await validateRepository(repo.root);
    const silent = structuredClone(repo.model);
    silent.entrance.audio = null;
    await unlink(path.join(repo.dir, 'entrance.mp3'));
    await repo.save(silent);
    await validateRepository(repo.root);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('a skin with a dynEntranceId must carry its entrance, and nothing else may', async () => {
  const repo = await entranceRepo();
  const variants = [
    // The guard this exists for: the skin_table names an entrance, the model left it out.
    [(m) => { m.entrance = null; }, /has the entrance dyn_entrance_char_9999_test_unit#1 but model\.json has none/],
    [(m) => { m.dynEntranceId = null; }, /entrance given for a skin without a dynEntranceId/],
    [(m) => { delete m.dynEntranceId; }, /dynEntranceId must be/],
    [(m) => { delete m.entrance; }, /entrance must be present/],
    [(m) => { m.dynEntranceId = 'dyn_illust_char_9999_test_unit#1'; }, /dynEntranceId must be/],
    [(m) => { m.entrance.animations = { Idle: 1.235 }; }, /entrance animations must include Start/],
    [(m) => { m.entrance.animations = { Start: 9 }; }, /entrance: animations differ/],
    [(m) => { m.entrance.bounds = { x: 0, y: 0, width: 1, height: 1 }; }, /entrance: bounds differ/],
    [(m) => { m.entrance.textures[0].file = 'page0.webp'; }, /entrance-page0\.webp/],
    [(m) => { m.entrance.skeleton.format = 'binary'; }, /entrance\.skel/],
    [(m) => { m.entrance.audio.duration = 0; }, /duration missing/],
    [(m) => { m.entrance.audio.sha256 = '0'.repeat(64); }, /entrance\.mp3 sha256 does not match/],
    // The camera: always written (null when the prefab names none), well formed, within the entrance.
    [(m) => { delete m.entrance.camera; }, /entrance\.camera must be present/],
    [(m) => { m.entrance.camera = { frames: [], fades: [], handover: null }; }, /camera must be null or/],
    [(m) => { delete m.entrance.camera.handover; }, /camera must be null or/],
    [(m) => { m.entrance.camera.handover = [1, 1]; }, /handover must be/],
    [(m) => { m.entrance.camera.frames[1] = m.entrance.camera.frames[1].slice(0, 4); }, /roll in degrees/],
    [(m) => { m.entrance.camera.frames[1][4] = 200; }, /roll in degrees/],
    [(m) => { m.entrance.camera.frames[0][0] = 0.1; }, /camera\.frames must start at 0/],
    [(m) => { m.entrance.camera.frames[1][3] = 0; }, /height > 0/],
    [(m) => { m.entrance.camera.frames.push([0.5, 0, 0, 1, 0]); }, /out of order or past the entrance/],
    [(m) => { m.entrance.camera.frames[1][0] = 99; }, /out of order or past the entrance/],
    [(m) => { m.entrance.camera.fades[0].color = [0, 0, 2]; }, /color must be/],
    [(m) => { m.entrance.camera.fades[0].keys[1][1] = 1.5; }, /alpha 0-1/],
    [(m) => { m.entrance.camera.fades[0].keys = []; }, /keys missing/],
  ];
  try {
    for (const [mutate, message] of variants) {
      const model = structuredClone(repo.model);
      mutate(model);
      await repo.save(model);
      await assert.rejects(validateRepository(repo.root), message, `expected ${message}`);
    }
    // A leftover entrance file in a folder without an entrance is a stray file.
    const plain = structuredClone(repo.model);
    plain.dynEntranceId = null;
    plain.entrance = null;
    await repo.save(plain);
    await assert.rejects(validateRepository(repo.root), /unexpected files/);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('the soundtrack must really be an MP3', async () => {
  assert.equal(isMp3(Buffer.from('ID3\x04\x00\x00\x00')), true);
  assert.equal(isMp3(Buffer.from([0xff, 0xfb, 0x90, 0x64, 0])), true);
  assert.equal(isMp3(Buffer.from('OggS\x00\x02')), false);
  const repo = await entranceRepo();
  try {
    const wav = Buffer.from('RIFF....WAVEfmt ');
    await writeFile(path.join(repo.dir, 'entrance.mp3'), wav);
    const model = structuredClone(repo.model);
    model.entrance.audio = record('entrance.mp3', wav, { duration: 22.772 });
    await repo.save(model);
    await assert.rejects(validateRepository(repo.root), /entrance\.mp3 is not an MP3/);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

/** The fixture model with layers: a backdrop behind the skeleton and a glow on its bone in front,
 *  both on the tiny page texture (4x4) as layer0.webp. */
async function layersRepo() {
  const repo = await fixtureRepo();
  const dir = path.join(repo.root, repo.folder);
  const texture = await readFile(path.join(fixtures, 'tiny', 'page0.webp'));
  await writeFile(path.join(dir, 'layer0.webp'), texture);
  const quad = { uvs: [0, 1, 1, 1, 0, 0, 1, 0], colors: null, triangles: [0, 3, 1, 3, 0, 2], scroll: null, only: null, delay: 0, approximated: null };
  const doc = {
    schemaVersion: 1,
    textures: [{ ...record('layer0.webp', texture, { width: 4, height: 4 }), wrap: ['clamp', 'clamp'], opaque: [0, 0, 1, 1] }],
    bounds: null,
    separators: [],
    draw: [
      { layer: { ...quad, name: 'sky', blend: 'alpha', texture: 0, color: [1, 1, 1, 1], vertices: [-300, -10, 300, -10, -300, 400, 300, 400], follow: null, animation: null } },
      { part: 0 },
      { layer: { ...quad, name: 'glow', blend: 'add', texture: 0, color: null, vertices: [-1, -1, 1, -1, -1, 1, 1, 1],
        follow: { bone: 'body', xy: true, rotation: true, localScale: false, mirrored: false, parent: [100, 0, 0, 100], position: [0, 0], angle: 0 },
        animation: { length: 1, loop: true, loopFrom: 0, frames: [[0, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0], [1, 2, 0, 0, 2, 0, 0, 1, 1, 1, 0, 1, 1, 0, 1, 0]] } } },
    ],
    omitted: { particles: 3, trails: 0, skinned: 0, hidden: 1, holders: 0, custom: [{ name: 'cloud', reason: 'flow distortion (up to 0.01 UV, 5 texels)' }], externalTexture: [], other: [] },
  };
  const skeleton = await readFile(path.join(dir, 'skeleton.json'));
  const atlas = await readFile(path.join(dir, 'skeleton.atlas'), 'utf8');
  doc.bounds = inspectLayers(skeleton, atlas, doc, 'test').bounds;
  const save = async (d = doc, m = null) => {
    const bytes = Buffer.from(JSON.stringify(d));
    await writeFile(path.join(dir, 'layers.json'), bytes);
    const model = m ?? structuredClone(repo.model);
    if (!m) model.layers = record('layers.json', bytes);
    await repo.save(model);
  };
  await save();
  return { ...repo, dir, doc, saveLayers: save };
}

test('layers validate with their texture, and frame the skeleton with the layers drawn at rest', async () => {
  const repo = await layersRepo();
  try {
    // The sky reaches past the skeleton (x -50..50, y 0..200); the glow on the body bone (at y 100)
    // spans 100 units each way at its first frame.
    assert.deepEqual(repo.doc.bounds, { x: -300, y: -10, width: 600, height: 410 });
    assert.deepEqual(await validateRepository(repo.root), { listed: 1, folders: 1, failures: 0 });
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('broken layers fail validation', async () => {
  const repo = await layersRepo();
  const layer = (d, i) => d.draw[i].layer;
  const variants = [
    [(d) => { d.bounds = { x: 0, y: 0, width: 1, height: 1 }; }, /layers bounds differ/],
    [(d) => { d.schemaVersion = 2; }, /schemaVersion must be 1/],
    [(d) => { d.textures[0].file = '../layer0.webp'; }, /layer0\.webp/],
    [(d) => { d.textures[0].sha256 = '0'.repeat(64); }, /layer0\.webp sha256 does not match/],
    [(d) => { d.textures[0].width = 8; }, /layer0\.webp is 4x4/],
    [(d) => { d.textures[0].wrap = ['clamp', 'mirror-once']; }, /wrap/],
    [(d) => { d.textures[0].opaque = [0.5, 0, 0.5, 1]; }, /opaque must be/],
    [(d) => { d.textures[0].opaque = [0, 0, 1, 1.5]; }, /opaque must be/],
    // The frame is fitted to what shows: a smaller opaque box changes the bounds.
    [(d) => { d.textures[0].opaque = [0.25, 0.25, 0.75, 0.75]; }, /layers bounds differ/],
    [(d) => { layer(d, 0).texture = 1; }, /texture 1 is not in textures/],
    [(d) => { layer(d, 0).blend = 'multiply'; }, /blend must be alpha or add/],
    [(d) => { layer(d, 0).uvs.pop(); }, /uvs must pair/],
    [(d) => { layer(d, 0).triangles[2] = 4; }, /triangles must index/],
    [(d) => { layer(d, 0).colors = [1, 1, 1]; }, /colors must be null/],
    [(d) => { layer(d, 0).vertices[0] = 1e7; }, /out of reach/],
    [(d) => { layer(d, 0).color = null; }, /color must be/],
    [(d) => { layer(d, 2).color = [1, 1, 1, 1]; }, /takes its colour from its frames/],
    [(d) => { layer(d, 2).animation.frames[0][0] = 0.1; }, /frames must start at 0/],
    [(d) => { layer(d, 2).animation.frames[1][0] = 2; }, /past the timeline/],
    [(d) => { layer(d, 2).animation.frames[1][11] = 0.5; }, /active must be 0 or 1/],
    [(d) => { layer(d, 2).animation.frames[1].pop(); }, /16 numbers/],
    [(d) => { layer(d, 2).animation.states = { Idle: structuredClone(layer(d, 2).animation) }; }, /not a triggered animation/],
    [(d) => { layer(d, 2).follow.bone = 'tail'; }, /follows bone tail/],
    [(d) => { delete layer(d, 2).follow.parent; }, /follow must be/],
    [(d) => { layer(d, 0).only = 'Touch'; }, /only must be/],
    [(d) => { layer(d, 0).delay = -1; }, /delay must be/],
    [(d) => { delete layer(d, 0).approximated; }, /approximated must be null or a note/],
    [(d) => { layer(d, 0).approximated = ' '; }, /approximated must be null or a note/],
    [(d) => { d.separators = ['tail']; }, /separator slot tail is not in the skeleton/],
    [(d) => { d.separators = ['body', 'body']; }, /separators must be distinct/],
    [(d) => { d.draw[1].part = 1; }, /part must be a distinct index 0-0/],
    [(d) => { d.draw.push({ part: 0 }); }, /part must be a distinct index/],
    [(d) => { d.draw.splice(1, 1); }, /no skeleton part/],
    [(d) => { d.draw = [d.draw[1]]; }, /layer0\.webp is not drawn by any plain layer/],
    [(d) => { delete d.omitted.holders; }, /omitted must give/],
    [(d) => { d.omitted.custom.push({ name: 'x', reason: '' }); }, /omitted must give/],
  ];
  try {
    for (const [mutate, message] of variants) {
      const doc = structuredClone(repo.doc);
      mutate(doc);
      await repo.saveLayers(doc);
      await assert.rejects(validateRepository(repo.root), message, `expected ${message}`);
    }
    await repo.saveLayers();
    await validateRepository(repo.root);
    // A layer texture model.json's layers.json does not list is a stray file.
    const bare = structuredClone(repo.model);
    bare.layers = null;
    await unlink(path.join(repo.dir, 'layers.json'));
    await repo.save(bare);
    await assert.rejects(validateRepository(repo.root), /unexpected files layer0\.webp/);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

/** layersVersion 2: the sky is approximated (a slight flow drawn without it) and carries its exact
 *  effect; a flame drawn only with its effect (a dissolve) sits in front, past the skeleton's right.
 *  layer1.webp is the noise and dissolve texture only effects sample. */
async function effectsRepo() {
  const repo = await layersRepo();
  const texture = await readFile(path.join(fixtures, 'tiny', 'page0.webp'));
  await writeFile(path.join(repo.dir, 'layer1.webp'), texture);
  const map = (extra = {}) => ({ texture: 1, st: [1, 1, 0, 0], speed: [0, 0.1], scroll: [0, 0], ...extra });
  const shader = (extra = {}) => ({ family: 'particle', main: { st: [1, 1, 0, 0], speed: [0, 0], scroll: [0, 0], fract: false },
    distort: null, dissolve: [], edge: null, ramp: null, vertex: null, animated: [], ...extra });
  const doc = structuredClone(repo.doc);
  doc.effectTextures = [{ ...record('layer1.webp', texture, { width: 4, height: 4 }), wrap: ['repeat', 'repeat'], opaque: [0, 0, 1, 1] }];
  doc.effectBounds = null;
  doc.draw[0].layer.approximated = 'flow distortion (up to 0.01 UV, 5 texels) drawn without it';
  doc.draw[0].layer.exact = { uvs: [0, 0, 1, 0, 0, 1, 1, 1], shader: shader({
    distort: { space: 'main', main: 1, dissolve: 0, constant: [0, 0], maps: [map({ anchor: [0, 0], intensity: [0.01, 0] })], weight: null } }) };
  doc.draw.push({ effect: { name: 'flame', blend: 'add', texture: 0, color: [1, 1, 1, 1], vertices: [100, 0, 500, 0, 100, 100, 500, 100], uvs: [0, 0, 1, 0, 0, 1, 1, 1],
    colors: null, triangles: [0, 3, 1, 3, 0, 2], follow: null, animation: null, only: null, delay: 0, cull: 0, visible: [0, 0, 1, 1],
    shader: shader({ dissolve: [map({ fract: false, amount: 0.3, border: 0.1 })] }) } });
  const skeleton = await readFile(path.join(repo.dir, 'skeleton.json'));
  const atlas = await readFile(path.join(repo.dir, 'skeleton.atlas'), 'utf8');
  doc.bounds = null;
  Object.assign(doc, inspectLayers(skeleton, atlas, doc, 'test'));
  const model = structuredClone(repo.model);
  const save = async (d = doc, version = 2) => {
    const bytes = Buffer.from(JSON.stringify(d));
    await writeFile(path.join(repo.dir, 'layers.json'), bytes);
    await repo.save({ ...model, layers: record('layers.json', bytes), layersVersion: version });
  };
  await save();
  return { ...repo, doc, saveEffects: save, shader, map };
}

test('layersVersion 2: effects validate, and only the effect frame includes them', async () => {
  const repo = await effectsRepo();
  try {
    assert.deepEqual(repo.doc.bounds, { x: -300, y: -10, width: 600, height: 410 }, 'what every reader draws');
    assert.deepEqual(repo.doc.effectBounds, { x: -300, y: -10, width: 800, height: 410 }, 'with the flame, out to x 500');
    assert.deepEqual(await validateRepository(repo.root), { listed: 1, folders: 1, failures: 0 });
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('broken effects fail validation', async () => {
  const repo = await effectsRepo();
  const sky = (d) => d.draw[0].layer;
  const flame = (d) => d.draw[3].effect;
  const variants = [
    [(d) => { d.effectBounds = d.bounds; }, /effectBounds differ/],
    [(d) => { delete d.effectBounds; }, /must have effectTextures and effectBounds/],
    [(d) => { d.effectTextures[0].file = 'layer0.webp'; }, /must be layer1\.webp/],
    [(d) => { sky(d).texture = 1; }, /texture 1 is not in textures/],
    [(d) => { flame(d).texture = 2; }, /texture 2 is not in textures/],
    [(d) => { flame(d).shader.dissolve = []; sky(d).exact.shader.distort.maps[0].texture = null; }, /layer1\.webp is not sampled by any effect/],
    [(d) => { flame(d).scroll = null; }, /an effect has no scroll/],
    [(d) => { flame(d).shader.family = 'ripple'; }, /shader family must be/],
    [(d) => { flame(d).shader.extra = 1; }, /a particle shader is/],
    [(d) => { flame(d).shader.dissolve[0].border = 0; }, /border > 0/],
    [(d) => { flame(d).shader.dissolve.push(...[1, 2].map(() => structuredClone(flame(d).shader.dissolve[0]))); }, /up to 2/],
    [(d) => { flame(d).shader.dissolve[0].st = [1, 1]; }, /st \[sx, sy, ox, oy\]/],
    [(d) => { flame(d).shader.edge = { color: [1, 1, 1, 1], pow: 1, epsilon: true }; flame(d).shader.dissolve = []; d.effectTextures = []; sky(d).exact.shader.distort.maps[0].texture = 0; }, /an edge needs a dissolve/],
    [(d) => { flame(d).shader.distort = { space: 'sideways', main: 1, dissolve: 0, constant: [0, 0], maps: [], weight: null }; }, /distort must be/],
    [(d) => { flame(d).shader.distort = { space: 'raw', main: 1, dissolve: 0, constant: [0, 0], maps: [], weight: null }; }, /moves nothing/],
    [(d) => { flame(d).shader.vertex = { ...repo.map(), intensity: [1, 1], weight: null, matrix: [1, 0, 0, 0, 1, 0] }; }, /vertex intensity/],
    [(d) => { flame(d).shader = { family: 'noise', mode: 'glow', main: flame(d).shader.main, noise: repo.map(), noise1: [1, 1, 1, 1], noise2: [1, 1, 1, 1], glow: null, animated: [] }; }, /glow must be/],
    [(d) => { flame(d).shader.animated = ['dissolve.0.amount']; }, /a layer without a timeline animates no parameter/],
    [(d) => { flame(d).cull = 2; }, /cull must be 0, or 1 \(front\) or 2 \(back\) for a layer that moves/],
    [(d) => { delete flame(d).visible; }, /visible must be null or/],
    // An effect frames by where it shows at its first frame: nowhere, and the frame leaves it out.
    [(d) => { flame(d).visible = null; }, /effectBounds differ/],
    [(d) => { flame(d).visible = [0, 0, 0.5, 1]; }, /effectBounds differ/],
    [(d) => { delete flame(d).cull; }, /cull must be/],
    [(d) => { flame(d).shader.animated = ['dissolve.1.amount']; }, /animated dissolve\.1\.amount is not a parameter/],
    [(d) => { flame(d).shader.animated = ['dissolve.0.amount', 'dissolve.0.amount']; }, /distinct parameter paths/],
    [(d) => { sky(d).exact.shader.animated = ['distort.maps.0.intensity']; }, /an exact effect animates no parameter/],
    [(d) => { flame(d).shader.animated = ['dissolve.0.st']; flame(d).animation = { length: 1, loop: true, loopFrom: 0, frames: [[0, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0]] }; flame(d).color = null; }, /frames\[0\] must be 20 numbers/],
    [(d) => { sky(d).shader = sky(d).exact.shader; }, /a plain layer has no shader/],
    [(d) => { sky(d).approximated = null; }, /only an approximated layer has an exact effect/],
    [(d) => { sky(d).exact.uvs.pop(); }, /exact must be/],
  ];
  try {
    // A dissolve an Animator drives: its amount and tiling ride on the frames, after the 16 numbers.
    const moving = structuredClone(repo.doc);
    Object.assign(flame(moving), { color: null, animation: { length: 1, loop: true, loopFrom: 0,
      frames: [[0, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0, 0.1, 1, 1, 0, 0], [1, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0, 0.9, 1, 1, 0, 0.5]] } });
    flame(moving).shader.animated = ['dissolve.0.amount', 'dissolve.0.st'];
    await repo.saveEffects(moving);
    await validateRepository(repo.root);
    for (const [mutate, message] of variants) {
      const doc = structuredClone(repo.doc);
      mutate(doc);
      await repo.saveEffects(doc);
      await assert.rejects(validateRepository(repo.root), message, `expected ${message}`);
    }
    // The version model.json names must match what layers.json holds.
    await repo.saveEffects(repo.doc, 1);
    await assert.rejects(validateRepository(repo.root), /says layersVersion 1/);
    await repo.saveEffects(repo.doc, 4);
    await assert.rejects(validateRepository(repo.root), /layersVersion must be 1-3/);
    const plain = structuredClone(repo.doc);
    delete plain.effectTextures;
    delete plain.effectBounds;
    delete plain.draw[0].layer.exact;
    await repo.saveEffects(plain, 1);
    await assert.rejects(validateRepository(repo.root), /must be \{ part \} or \{ layer \}$/);
    await repo.saveEffects();
    await validateRepository(repo.root);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});

test('layersVersion 3: a tilted entry validates, frames the effect bounds in 3D, and needs version 3', async () => {
  const repo = await effectsRepo();
  try {
    const doc = structuredClone(repo.doc);
    // The flame as a mesh turning in depth: x, y, z vertices; frames [t, a, b, c, d, tx, ty, rgba, active, uv, e, f, offset].
    const flame = doc.draw[3].effect;
    delete doc.draw[3].effect;
    const frame = (t, e) => [t, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0, e, 0, 0.1 * t, 0, 0.1, 0];
    doc.draw[3].tilted = { ...flame, color: null, vertices: [100, 0, 0, 500, 0, 0, 100, 100, 0, 500, 100, 100],
      animation: { length: 1, loop: true, loopFrom: 0, frames: [frame(0, 0), frame(1, 2)] },
      shader: { ...flame.shader, animated: ['main.offset'] } };
    const skeleton = await readFile(path.join(repo.dir, 'skeleton.json'));
    const atlas = await readFile(path.join(repo.dir, 'skeleton.atlas'), 'utf8');
    Object.assign(doc, inspectLayers(skeleton, atlas, { ...doc, bounds: null, effectBounds: null }, 'test'));
    await repo.saveEffects(doc, 3);
    assert.deepEqual(await validateRepository(repo.root), { listed: 1, folders: 1, failures: 0 });
    await repo.saveEffects(doc, 2);
    await assert.rejects(validateRepository(repo.root), /must be \{ part \}, \{ layer \} or \{ effect \}/);
    const flat = structuredClone(doc);
    flat.draw[3].tilted.vertices = flat.draw[3].tilted.vertices.slice(0, 8);
    await repo.saveEffects(flat, 3);
    await assert.rejects(validateRepository(repo.root), /vertices must be x, y, z triples/);
    const short = structuredClone(doc);
    short.draw[3].tilted.animation.frames = short.draw[3].tilted.animation.frames.map((f) => f.slice(0, 16));
    await repo.saveEffects(short, 3);
    await assert.rejects(validateRepository(repo.root), /must be 22 numbers/);
  } finally { await rm(repo.root, { recursive: true, force: true }); }
});
