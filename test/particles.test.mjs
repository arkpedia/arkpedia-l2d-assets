import test from 'node:test';
import assert from 'node:assert/strict';
import { layerBounds, layersShape, PARTICLES_FROM } from '../scripts/layers.mjs';
import { checkCoverage, colourShape, curveShape, particleCoverage } from '../scripts/particles.mjs';
import { readFileSync } from 'node:fs';

const sha = 'a'.repeat(64);
const texture = (i, extra = {}) => ({ file: `layer${i}.webp`, width: 4, height: 4, wrap: ['clamp', 'clamp'], opaque: [0, 0, 1, 1], bytes: 10, sha256: sha, ...extra });
const shader = (main = 0) => ({ family: 'particle', main: { st: [1, 1, 0, 0], speed: [0, 0], scroll: [0, 0], fract: false }, distort: null, dissolve: [], edge: null, ramp: null,
  vertex: null, animated: [], ...(main ? {} : {}) });
const system = (name, extra = {}) => ({
  name, requires: [], only: null, delay: 0, active: true, follow: null, child: false,
  emitter: { matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0], rotation: [0, 0, 0, 1], scale: [1, 1, 1] },
  clock: { maxAlive: 10 }, emission: {}, start: {}, render: { mode: 'billboard' }, material: 0, ...extra,
});

const plainLayer = { name: 'sky', blend: 'alpha', texture: 0, color: [1, 1, 1, 1], vertices: [0, 0, 1, 0, 0, 1], uvs: [0, 0, 1, 0, 0, 1], colors: null,
  triangles: [0, 1, 2], follow: null, animation: null, scroll: null, only: null, delay: 0, approximated: null };

/** A layers.json with one plain layer and two particle runs, and its layerParticles.json. */
function fixture() {
  const doc = {
    schemaVersion: 1, textures: [texture(0)], effectTextures: [], bounds: { x: 0, y: 0, width: 1, height: 1 }, effectBounds: { x: 0, y: 0, width: 1, height: 1 },
    particles: { file: 'layerParticles.json', bytes: 100, sha256: sha, version: 1 }, separators: [],
    draw: [{ particles: [0] }, { part: 0 }, { layer: plainLayer }, { particles: [1, 2] }],
    omitted: { particles: 1, trails: 0, skinned: 0, hidden: 0, holders: 0, custom: [], externalTexture: [], other: [], particleReasons: [{ name: 'x', reason: 'renderer off' }] },
  };
  const particles = {
    version: 1, unit: 0.01, camera: { size: 10.5, height: 2100 }, controller: { fixFxDelay: 1, animTimeFixed: 0 },
    textures: [texture(1, { phase: 'Interact' })],
    materials: [{ blend: 'add', texture: 0, color: [1, 1, 1, 1], shader: shader(), custom: null, cull: 0 },
      { blend: 'alpha', texture: 1, color: [2, 2, 2, 1], shader: shader(), custom: { inputs: ['c1.x', 'c1.y', 'c1.z', 'c1.w', 'c2.x', 'c2.y', 0, 0] }, cull: 0 }],
    meshes: [{ builtin: 'quad' }, { vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0], uvs: [0, 0, 1, 0, 0, 1], colors: null, triangles: [0, 1, 2], normals: [0, 0, -1, 0, 0, -1, 0, 0, -1], areaCdf: [1] }],
    systems: [
      system('a', { clock: { duration: 2, prewarm: true, prewarmWindow: 1.5, seed: 4294967295, maxAlive: 30 }, emission: { rate: ['r', 1, 3], bursts: [[0, ['c', 2, [0, 1, 0, null, 1, 0, 0, 0]], 1, 0.01, 1]] },
        start: { lifetime: ['cc', 1, [0, 1, 0, 0, 1, 2, 0, 0], [0, 2, 0, 0, 1, 3, 0, 0], ['w', 'clamp', 'clamp', 'repeat', 'clamp']], color: ['gg', { c: [0, 1, 1, 1], a: [0, 1] }, { c: [0, 0, 0, 0, 1, 1, 1, 1], a: [0, 1, 1, 0], fixed: true }] },
        shape: { type: 'cone', radius: 0.5, arc: 90, arcMode: { mode: 'loop', speed: ['r', 0.5, 1] }, angle: 10 },
        render: { mode: 'mesh', align: 'local', meshes: [0] }, material: 1, only: 'Interact' }),
      system('b', { shape: { type: 'mesh', mesh: 1, placement: 'triangle' }, sheet: { tiles: [2, 2], animation: 'singleRow', row: 'random', frameOverTime: ['c', 1, [0, 0, 1, 1, 1, 1, 1, 1]] },
        custom: [null, { vector: [['c', 1, [0, 0, 0, 0, 1, 1, 0, 0]]] }], noise: { strengthXYZ: [1, 2, 3], quality: 'medium' }, limit: { xyz: [1, 1, 1], world: true, dampen: 0.1 } }),
      system('c', { render: { mode: 'stretched', lengthScale: -2, sort: 'youngestInFront', maxSize: 10 }, velocity: { linear: [0, 1, 0], world: true }, size: { xyz: [1, 2, 3] } }),
    ],
  };
  return { doc, particles };
}

test('a layers.json with particle runs validates with its layerParticles.json and returns every texture', () => {
  const { doc, particles } = fixture();
  const textures = layersShape(doc, 'f', PARTICLES_FROM, particles);
  assert.deepEqual(textures.map((t) => t.file), ['layer0.webp', 'layer1.webp']);
});

test('the particle checks refuse what a reader could not trust', () => {
  const cases = [
    ['an older layersVersion', (d) => { d.version = PARTICLES_FROM - 1; }, /has none/],
    ['a pointer to another file', (d) => { d.doc.particles.file = '../x.json'; }, /particles must be null or/],
    ['the file not read', (d) => { d.particles = null; }, /not read/],
    ['a system in no run', (d) => { d.doc.draw[3].particles = [1]; }, /every drawn system once/],
    ['runs out of order', (d) => { d.doc.draw[0].particles = [2]; d.doc.draw[3].particles = [0, 1]; }, /every drawn system once/],
    ['two runs side by side', (d) => { d.doc.draw = [{ particles: [0] }, { particles: [1, 2] }, { part: 0 }, { layer: plainLayer }]; }, /maximal/],
    ['an index past the systems', (d) => { d.doc.draw[3].particles = [1, 9]; }, /must list systems/],
    ['an unknown capability', (d) => { d.particles.systems[0].requires = ['collision']; }, /requires/],
    ['an unknown member', (d) => { d.particles.systems[1].lights = {}; }, /unknown member lights/],
    ['a prewarmed loop without its window', (d) => { delete d.particles.systems[0].clock.prewarmWindow; }, /prewarmWindow/],
    ['a texture numbered out of turn', (d) => { d.particles.textures[0].file = 'layer5.webp'; }, /must be layer1.webp/],
    ['a texture no material samples', (d) => { d.particles.materials[1].texture = 0; }, /not sampled/],
    ['an unused material', (d) => { d.particles.systems[0].material = 0; }, /used by no system/],
    ['a shape mesh without its areas', (d) => { d.particles.meshes[1].areaCdf = null; d.particles.meshes[1].normals = null; }, /areaCdf exactly when a shape/],
    ['an unknown custom input', (d) => { d.particles.materials[1].custom.inputs[0] = 'agePercent'; }, /custom must be/],
    ['a curve with a bad tag', (d) => { d.particles.systems[2].emission.rate = ['q', 1]; }, /unknown curve/],
    ['a stretched member on a billboard', (d) => { d.particles.systems[0].render = { mode: 'billboard', lengthScale: 2 }; d.particles.systems[0].material = 1; }, /for stretched/],
    ['reasons that do not add up', (d) => { d.doc.omitted.particles = 2; }, /particleReasons/],
    ['a file past the size limit', (d) => { d.doc.particles.bytes = 2 * 1024 * 1024; }, /at most/],
    ['too many systems', (d) => { d.particles.systems = Array.from({ length: 2049 }, (_, i) => system(`s${i}`)); }, /at most 128 and 2048/],
    ['an unknown timeline column', (d) => { d.particles.systems[2].emitter = { timeline: { columns: ['t', 'matrix', 'rotation', 'scale', 'active', 'colour'], length: 0, loop: false, loopFrom: 0, frames: [[0]] } }; }, /unknown column colour/],
  ];
  for (const [what, change, error] of cases) {
    const fix = { ...fixture(), version: PARTICLES_FROM };
    change(fix);
    assert.throws(() => layersShape(fix.doc, 'f', fix.version, fix.particles), error, what);
  }
});

test('an emitter timeline names its columns and carries their widths', () => {
  const { doc, particles } = fixture();
  const frame = (t, active) => [t, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 1, active, 1, 1, 1, 1, 0.5];
  particles.systems[2].emitter = { timeline: { columns: ['t', 'matrix', 'rotation', 'scale', 'active', 'tint', 'dissolve.0.amount'], length: 1, loop: true, loopFrom: 0,
    frames: [frame(0, 1), frame(1, 0)], states: { Interact: { length: 0, loop: false, loopFrom: 0, frames: [frame(0, 1)] } } } };
  layersShape(doc, 'f', PARTICLES_FROM, particles);
  particles.systems[2].emitter.timeline.frames[1][20] = 0.5;
  assert.throws(() => layersShape(doc, 'f', PARTICLES_FROM, particles), /active must be 0 or 1/);
});

/** A family (a spawner drawing nothing, two children), a system with trail data and a TrailRenderer. */
function family() {
  const { doc, particles } = fixture();
  const trail = { mode: 'perParticle', ratio: 1, lifetime: 0.5, minVertexDistance: 0.1, textureMode: 'stretch', ribbonCount: 1, worldSpace: false,
    dieWithParticles: true, sizeAffectsWidth: true, sizeAffectsLifetime: false, inheritParticleColor: true, colorOverLifetime: [1, 1, 1, 1],
    widthOverTrail: 1, colorOverTrail: [1, 1, 1, 1], attachRibbonsToTransform: false, splitSubEmitterRibbons: false, material: 1 };
  particles.systems = [
    system('ctrl', { requires: ['sub'], render: null, material: null, sub: [[1, 'birth', 1], [2, 'death', 0.5]] }),
    system('ctrl/fire', { requires: ['sub'], child: true }),
    system('ctrl/spark', { requires: ['sub', 'trail'], child: true, material: 1, trail }),
    system('streaks', { requires: ['trail'], render: null, material: null, trail }),
  ];
  particles.meshes = [];
  particles.trails = [{ name: 'Trail', requires: ['trail'], only: null, delay: 0, active: true, follow: null,
    emitter: { matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0], rotation: [0, 0, 0, 1], scale: [1, 1, 1] }, time: 0.3, minVertexDistance: 0.1, widthMultiplier: 0.3,
    widthCurve: ['c', 1, [0, 1, 0, 0]], colorGradient: { c: [0, 1, 1, 1], a: [0, 1] }, numCornerVertices: 0, numCapVertices: 0, alignment: 'view',
    textureMode: 'stretch', emitting: true, material: 0, draw: [3, 1] }];
  doc.draw = [{ part: 0 }, { particles: [1] }, { layer: plainLayer }, { particles: [2] }];
  particles.trails[0].draw = [2, 0];
  return { doc, particles };
}

test('sub-emitter families, spawners and trails validate, and broken ones do not', () => {
  const { doc, particles } = family();
  layersShape(doc, 'f', PARTICLES_FROM, particles);
  const frame = (t, enabled, colour = []) => [t, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, enabled, ...colour];
  const cases = [
    ['a link to a system that is not a child', (d) => { d.particles.systems[0].sub[0][0] = 3; }, /not a sub-emitter/],
    ['a child no system links', (d) => { d.particles.systems[0].sub.pop(); }, /no system links/],
    ['a child with links of its own', (d) => { d.particles.systems[1].sub = [[2, 'birth', 1]]; }, /no sub-emitters of its own/],
    ['a probability past 1', (d) => { d.particles.systems[0].sub[1][2] = 1.5; }, /probability 0-1/],
    ['a system that draws nothing for nothing', (d) => { delete d.particles.systems[3].trail; d.particles.systems[3].requires = []; }, /spawns sub-emitters or carries trails/],
    ['a trail without its capability', (d) => { d.particles.systems[3].requires = []; }, /requires trail exactly/],
    ['a trail in an unknown mode', (d) => { d.particles.systems[3].trail.mode = 'comet'; }, /mode or textureMode/],
    ['a trail colour column without trails', (d) => { d.particles.systems[1].emitter = { timeline: { columns: ['t', 'matrix', 'rotation', 'scale', 'active', 'trail.color'], length: 0, loop: false, loopFrom: 0, frames: [[0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1]] } }; }, /drives nothing this record has/],
    ['a tint on a system that draws nothing', (d) => { d.particles.systems[0].emitter = { timeline: { columns: ['t', 'matrix', 'rotation', 'scale', 'active', 'tint'], length: 0, loop: false, loopFrom: 0, frames: [[0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1]] } }; }, /drives nothing this record has/],
    ['a switch half on', (d) => { d.particles.systems[1].emitter = { timeline: { columns: ['t', 'matrix', 'rotation', 'scale', 'active', 'emission.enabled'], length: 1, loop: false, loopFrom: 0, frames: [frame(0, 1), frame(1, 0.5)] } }; }, /emission.enabled must be 0 or 1/],
    ['a TrailRenderer drawn past the list', (d) => { d.particles.trails[0].draw = [9, 0]; }, /not a place in the draw list/],
    ['a TrailRenderer after a whole run', (d) => { d.particles.trails[0].draw = [1, 1]; }, /not a place in the draw list/],
    ['a TrailRenderer inside a layer', (d) => { d.particles.trails[0].draw = [2, 1]; }, /not a place in the draw list/],
    ['a TrailRenderer without its material', (d) => { d.particles.trails[0].material = 7; }, /not in materials/],
  ];
  for (const [what, change, error] of cases) {
    const fix = family();
    change(fix);
    assert.throws(() => layersShape(fix.doc, 'f', PARTICLES_FROM, fix.particles), error, what);
  }
});

test('coverage counts what the game draws and what an export carries, and a drop below the baseline fails', () => {
  const { doc, particles } = family();
  doc.omitted.particleReasons = [{ name: 'a', reason: 'renderer off' }, { name: 'b', reason: 'Collision module' }, { name: 'c (TrailRenderer)', reason: 'no material' }];
  // Drawn: the two children (the spawner and the trails-only system draw nothing) and the left-out Collision system.
  const counts = particleCoverage(doc, particles);
  assert.deepEqual(counts, { drawn: 3, exported: 2, v1: 1 });
  checkCoverage(counts, { drawn: 3, exported: 2, v1: 1 }, 'f');
  checkCoverage(counts, undefined, 'f');
  assert.throws(() => checkCoverage(counts, { drawn: 3, exported: 2, v1: 2 }, 'f'), /below its baseline/);
  assert.throws(() => checkCoverage(counts, { drawn: 4, exported: 2, v1: 1 }, 'f'), /drawn by the game, the baseline says 4/);
});

test('the sync adds a coverage entry only for a folder the baseline does not know', async () => {
  const { addMissing } = await import('../scripts/particle_coverage.mjs');
  const models = { 'models/a/000000000001': { drawn: 3, exported: 2, v1: 2 } };
  const found = { 'models/a/000000000001': { drawn: 3, exported: 1, v1: 1 }, 'models/b/000000000002': { drawn: 5, exported: 5, v1: 4 } };
  const { models: after, added } = addMissing(models, found);
  assert.deepEqual(added, ['models/b/000000000002']);
  // An export below its entry keeps the entry (the validator fails it), never lowers it.
  assert.deepEqual(after, { 'models/a/000000000001': { drawn: 3, exported: 2, v1: 2 }, 'models/b/000000000002': { drawn: 5, exported: 5, v1: 4 } });
  assert.deepEqual(models, { 'models/a/000000000001': { drawn: 3, exported: 2, v1: 2 } }, 'not changed in place');
});

test('the coverage baseline is whole', () => {
  const baseline = JSON.parse(readFileSync(new URL('../particle-coverage.json', import.meta.url), 'utf8'));
  const sum = { drawn: 0, exported: 0, v1: 0 };
  for (const [folder, counts] of Object.entries(baseline.models)) {
    assert.match(folder, /^models\/[A-Za-z0-9_]+\/[a-f0-9]{12}$/);
    assert.ok(Number.isSafeInteger(counts.drawn) && counts.v1 <= counts.exported && counts.exported <= counts.drawn, folder);
    for (const key of Object.keys(sum)) sum[key] += counts[key];
  }
  assert.deepEqual(baseline.totals, sum);
});

test('particles never frame the view', () => {
  const { doc } = fixture();
  const skeleton = { data: { slots: [] }, findBone: () => null };
  assert.deepEqual(layerBounds(doc, skeleton, { x: 0, y: 0, width: 2, height: 3 }), { x: 0, y: 0, width: 2, height: 3 });
});

test('curve and colour values', () => {
  for (const ok of [1, ['r', 0, 1], ['c', 1, []], ['c', 2, [0, 1, null, 0]], ['cc', 1, [0, 0, 0, 0], [0, 1, 0, 0], ['w', 'pingPong', 'clamp', 'clamp', 'repeat']]]) curveShape(ok, 'v');
  for (const bad of [null, '1', ['r', 1], ['c', 1, [0, 1, 0]], ['c', 1, [1, 0, 0, 0, 0, 0, 0, 0]], ['c', 1, [], ['w', 'loop', 'clamp']]]) assert.throws(() => curveShape(bad, 'v'));
  for (const ok of [[1, 1, 1, 1], ['r', [0, 0, 0, 0], [1, 1, 1, 1]], ['g', { c: [0, 1, 1, 1], a: [0, 1] }], ['rg', { c: [0, 1, 1, 1], a: [0, 1], fixed: true }]]) colourShape(ok, 'c');
  for (const bad of [[1, 1, 1], ['g', { c: [0, 1, 1], a: [0, 1] }], ['g', { c: [0, 1, 1, 1], a: [0, 1], fixed: false }]]) assert.throws(() => colourShape(bad, 'c'));
});
