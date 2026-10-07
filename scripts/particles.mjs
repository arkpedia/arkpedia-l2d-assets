// layerParticles.json (scripts/particles.py): the illustration prefab's ParticleSystems as data, checked
// strictly. Used by scripts/layers.mjs layersShape, so the validator and the sync's own bounds step both
// read it. The format is in the README (layerParticles.json).
import { shaderShape } from './layers.mjs';

export const PARTICLES_FILE = 'layerParticles.json';
export const PARTICLES_VERSION = 1;
// Capability tags a system may require (a reader leaves out a system needing one it does not know).
export const REQUIRES = new Set(['sub', 'trail']);
export const PHASES = ['idle', 'Start', 'Interact', 'Special'];
// What one model's particles may ask of a reader: the file's bytes, its own textures (each a fetch and an
// upload) and its systems.
export const PARTICLE_LIMITS = { bytes: 1024 * 1024, textures: 128, systems: 2048 };
const ONLY = new Set(['Idle', 'Interact', 'Special', 'Start']);
const SHAPES = new Set(['sphere', 'sphereShell', 'hemisphere', 'hemisphereShell', 'cone', 'box', 'mesh', 'coneShell', 'coneVolume', 'coneVolumeShell',
  'circle', 'circleEdge', 'edge', 'boxShell', 'boxEdge', 'donut', 'rectangle']);
// The fields each shape type may carry besides its transform and modifiers (scripts/particles.py SHAPE_FIELDS).
const SHAPE_FIELDS = {
  sphere: ['radius', 'thickness'], sphereShell: ['radius'], hemisphere: ['radius', 'thickness'], hemisphereShell: ['radius'],
  cone: ['radius', 'thickness', 'arc', 'arcMode', 'angle'], coneShell: ['radius', 'arc', 'arcMode', 'angle'],
  coneVolume: ['radius', 'thickness', 'arc', 'arcMode', 'angle', 'length'], coneVolumeShell: ['radius', 'arc', 'arcMode', 'angle', 'length'],
  circle: ['radius', 'thickness', 'arc', 'arcMode'], circleEdge: ['radius', 'arc', 'arcMode'], edge: ['radius', 'radiusMode'],
  donut: ['radius', 'thickness', 'arc', 'arcMode', 'donutRadius'], box: [], boxShell: ['boxThickness'], boxEdge: ['boxThickness'], rectangle: [],
  mesh: ['mesh', 'placement', 'spawn', 'useColors', 'normalOffset'],
};
const SHAPE_COMMON = ['type', 'pos', 'rot', 'scale', 'randomDir', 'spherical', 'randomPos'];
const MULTI_MODES = new Set(['random', 'loop', 'pingPong', 'burstSpread']);
const WRAPS = new Set(['pingPong', 'repeat', 'clamp']);
const RENDER_MODES = new Set(['billboard', 'stretched', 'mesh']);
const ALIGNMENTS = new Set(['view', 'world', 'local', 'facing', 'velocity']);
const SORT_MODES = new Set(['none', 'distance', 'oldestInFront', 'youngestInFront', 'depth']);
const INPUT = /^(uv|uv2)\.[xy]$|^c[12]\.[xyzw]$/;
// Emitter timeline columns: the head, then the animated ones (each named, never positional) with their widths.
export const TIMELINE_COLUMNS = {
  t: 1, matrix: 12, rotation: 4, scale: 3, active: 1, tint: 4, speed: 1, 'emission.rate': 1, 'emission.enabled': 1, 'emission.distance': 1,
  'main.startColor': 4, 'main.startSize': 1, 'main.gravity': 1, 'noise.strength': 1, 'velocity.speedModifier': 1, 'shape.radius': 1, 'size.multiplier': 1,
};
// Every timeline starts with these (the static emitter's matrix, rotation and scale, then whether it is on).
const TIMELINE_HEAD = ['t', 'matrix', 'rotation', 'scale', 'active'];
const ACTIVE = 20; // the active column's index in a frame
// Material parameters a timeline may carry (type 199 bindings), as layers.json's effects name them.
const SHADER_COLUMN = /^(main\.(st|offset)|dissolve\.\d\.(amount|border|st|offset)|distort\.(main|dissolve|weight\.st)|distort\.maps\.\d\.(st|offset|intensity)|ramp\.st|edge\.color)$/;

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const index = (value, length) => Number.isSafeInteger(value) && value >= 0 && value < length;
const vec = (value, length) => Array.isArray(value) && value.length === length && value.every(finite);

/** Every key of `value` is in `allowed`, and every key of `required` is there. */
function only(value, label, allowed, required = []) {
  if (!isObject(value)) throw new Error(`${label} must be an object`);
  for (const key of Object.keys(value)) if (!allowed.includes(key)) throw new Error(`${label}: unknown member ${key}`);
  for (const key of required) if (!Object.hasOwn(value, key)) throw new Error(`${label}: ${key} missing`);
}

/** Flat [t, v, in, out, ...] curve keys; a stepped slope is null. */
function keysShape(keys, label) {
  if (!Array.isArray(keys) || keys.length % 4) throw new Error(`${label}: keys must be [t, v, in, out, ...]`);
  let last = -Infinity;
  for (let i = 0; i < keys.length; i += 4) {
    if (!finite(keys[i]) || !finite(keys[i + 1]) || !(keys[i + 2] === null || finite(keys[i + 2])) || !(keys[i + 3] === null || finite(keys[i + 3]))) {
      throw new Error(`${label}: key ${i / 4} must be [t, v, in, out] numbers (null: a stepped slope)`);
    }
    if (keys[i] < last) throw new Error(`${label}: keys out of order`);
    last = keys[i];
  }
}

/** A curve value: a number; ["r", min, max]; ["c", scalar, keys(, ["w", pre, post])]; ["cc", scalar, minKeys, maxKeys(, ["w", 4 wraps])]. */
export function curveShape(value, label) {
  if (finite(value)) return;
  if (!Array.isArray(value)) throw new Error(`${label} must be a number or a tagged curve`);
  const [tag] = value;
  const wrap = (tail, count) => {
    if (!tail.length) return;
    if (tail.length !== 1 || !Array.isArray(tail[0]) || tail[0][0] !== 'w' || tail[0].length !== count + 1 || !tail[0].slice(1).every((w) => WRAPS.has(w))) {
      throw new Error(`${label}: a curve ends with ["w", ${count} wrap modes] or nothing`);
    }
  };
  if (tag === 'r' && value.length === 3 && finite(value[1]) && finite(value[2])) return;
  if (tag === 'c' && value.length >= 3 && finite(value[1])) { keysShape(value[2], label); wrap(value.slice(3), 2); return; }
  if (tag === 'cc' && value.length >= 4 && finite(value[1])) { keysShape(value[2], label); keysShape(value[3], label); wrap(value.slice(4), 4); return; }
  throw new Error(`${label}: unknown curve ${JSON.stringify(value).slice(0, 60)}`);
}

function gradientShape(g, label) {
  only(g, label, ['c', 'a', 'fixed'], ['c', 'a']);
  if (!Array.isArray(g.c) || !g.c.length || g.c.length % 4 || g.c.length > 32 || !g.c.every(finite)) throw new Error(`${label}: c must be 1-8 [t, r, g, b]`);
  if (!Array.isArray(g.a) || !g.a.length || g.a.length % 2 || g.a.length > 16 || !g.a.every(finite)) throw new Error(`${label}: a must be 1-8 [t, a]`);
  if (Object.hasOwn(g, 'fixed') && g.fixed !== true) throw new Error(`${label}: fixed is written only when true`);
}

/** A colour value: [r, g, b, a]; ["r", c0, c1]; ["g", G]; ["gg", G0, G1]; ["rg", G]. */
export function colourShape(value, label) {
  if (vec(value, 4)) return;
  if (!Array.isArray(value)) throw new Error(`${label} must be a colour`);
  const [tag] = value;
  if (tag === 'r' && value.length === 3 && vec(value[1], 4) && vec(value[2], 4)) return;
  if ((tag === 'g' || tag === 'rg') && value.length === 2) { gradientShape(value[1], label); return; }
  if (tag === 'gg' && value.length === 3) { gradientShape(value[1], label); gradientShape(value[2], label); return; }
  throw new Error(`${label}: unknown colour ${JSON.stringify(value).slice(0, 60)}`);
}

const curves3 = (value, label) => {
  if (!Array.isArray(value) || value.length !== 3) throw new Error(`${label} must be three curves`);
  value.forEach((v, i) => curveShape(v, `${label}[${i}]`));
};

function multiShape(value, label) {
  only(value, label, ['mode', 'spread', 'speed']);
  if (Object.hasOwn(value, 'mode') && !MULTI_MODES.has(value.mode)) throw new Error(`${label}: mode must be ${[...MULTI_MODES].join(', ')}`);
  if (Object.hasOwn(value, 'spread') && !finite(value.spread)) throw new Error(`${label}: spread must be a number`);
  if (Object.hasOwn(value, 'speed')) curveShape(value.speed, `${label} speed`);
}

/** An emitter timeline: named columns, frames of their summed width, in time order (TIMELINE_COLUMNS). */
function timelineShape(value, label) {
  only(value, label, ['columns', 'length', 'loop', 'loopFrom', 'frames', 'states'], ['columns', 'length', 'loop', 'loopFrom', 'frames']);
  const columns = value.columns;
  if (!Array.isArray(columns) || TIMELINE_HEAD.some((name, i) => columns[i] !== name) || new Set(columns).size !== columns.length) {
    throw new Error(`${label}: columns must start ${TIMELINE_HEAD.join(', ')} and name each column once`);
  }
  let width = 0;
  for (const name of columns) {
    const shader = typeof name === 'string' && SHADER_COLUMN.test(name);
    if (!shader && !Object.hasOwn(TIMELINE_COLUMNS, name)) throw new Error(`${label}: unknown column ${name}`);
    width += shader ? { amount: 1, border: 1, main: 1, dissolve: 1, st: 4, offset: 4, intensity: 2, color: 4 }[name.split('.').at(-1)] : TIMELINE_COLUMNS[name];
  }
  const line = (timeline, at) => {
    if (!finite(timeline.length) || timeline.length < 0 || typeof timeline.loop !== 'boolean' || !finite(timeline.loopFrom) || timeline.loopFrom < 0
        || !Array.isArray(timeline.frames) || !timeline.frames.length) {
      throw new Error(`${at}: a timeline is { length >= 0, loop, loopFrom, frames }`);
    }
    let last = -Infinity;
    timeline.frames.forEach((frame, i) => {
      if (!vec(frame, width)) throw new Error(`${at}: frames[${i}] must be ${width} numbers`);
      if (!(frame[0] > last)) throw new Error(`${at}: frames[${i}] is out of order`);
      if (frame[ACTIVE] !== 0 && frame[ACTIVE] !== 1) throw new Error(`${at}: frames[${i}] active must be 0 or 1`);
      last = frame[0];
    });
    if (timeline.frames[0][0] !== 0) throw new Error(`${at}: frames must start at 0`);
  };
  line(value, label);
  if (Object.hasOwn(value, 'states')) {
    if (!isObject(value.states) || !Object.keys(value.states).length) throw new Error(`${label}: states must name animations`);
    for (const [name, state] of Object.entries(value.states)) {
      if (!ONLY.has(name) || name === 'Idle') throw new Error(`${label}: states.${name} is not a triggered animation`);
      only(state, `${label} states.${name}`, ['length', 'loop', 'loopFrom', 'frames'], ['length', 'loop', 'loopFrom', 'frames']);
      line(state, `${label} states.${name}`);
    }
  }
}

function followShape(f, label) {
  if (!isObject(f) || typeof f.bone !== 'string' || !f.bone || !['xy', 'rotation', 'localScale', 'mirrored'].every((k) => typeof f[k] === 'boolean')
      || !vec(f.parent, 4) || !vec(f.position, 2) || !finite(f.angle)) {
    throw new Error(`${label}: follow must be { bone, xy, rotation, localScale, mirrored, parent: [a, b, c, d], position: [x, y], angle }`);
  }
}

/** One system of layerParticles.json. */
function systemShape(s, label, counts) {
  only(s, label, ['name', 'requires', 'only', 'delay', 'active', 'follow', 'child', 'emitter', 'clock', 'emission', 'start', 'shape', 'velocity', 'limit',
    'force', 'noise', 'inherit', 'size', 'rotation', 'color', 'sheet', 'custom', 'sub', 'render', 'material', 'trail'],
  ['name', 'requires', 'only', 'delay', 'active', 'follow', 'child', 'emitter', 'clock', 'emission', 'start', 'render', 'material']);
  if (typeof s.name !== 'string' || !s.name) throw new Error(`${label}: name missing`);
  if (!Array.isArray(s.requires) || !s.requires.every((tag) => REQUIRES.has(tag)) || new Set(s.requires).size !== s.requires.length) {
    throw new Error(`${label}: requires must be distinct tags of ${[...REQUIRES].join(', ')}`);
  }
  if (s.only !== null && !ONLY.has(s.only)) throw new Error(`${label}: only must be null or ${[...ONLY].join(', ')}`);
  if (!finite(s.delay) || s.delay < 0) throw new Error(`${label}: delay must be seconds >= 0`);
  if (typeof s.active !== 'boolean' || typeof s.child !== 'boolean') throw new Error(`${label}: active and child must be true or false`);
  if (s.follow !== null) followShape(s.follow, `${label} follow`);
  if (s.child && !s.requires.includes('sub')) throw new Error(`${label}: a sub-emitter child requires sub`);
  if (Object.hasOwn(s, 'sub') !== s.requires.includes('sub') && !s.child) throw new Error(`${label}: requires sub exactly with sub-emitters`);
  if (Object.hasOwn(s, 'trail') !== s.requires.includes('trail')) throw new Error(`${label}: requires trail exactly with trail data`);
  const e = s.emitter;
  if (isObject(e) && Object.hasOwn(e, 'timeline')) {
    only(e, `${label} emitter`, ['timeline'], ['timeline']);
    timelineShape(e.timeline, `${label} emitter.timeline`);
  } else {
    only(e, `${label} emitter`, ['matrix', 'rotation', 'scale'], ['matrix', 'rotation', 'scale']);
    if (!vec(e.matrix, 12) || !vec(e.rotation, 4) || !vec(e.scale, 3)) throw new Error(`${label}: emitter matrix must be 12 numbers (3x4), rotation a quaternion and scale [x, y, z]`);
  }
  const c = s.clock;
  only(c, `${label} clock`, ['duration', 'loop', 'prewarm', 'prewarmWindow', 'startDelay', 'speed', 'maxParticles', 'seed', 'space', 'scaling', 'maxAlive'], ['maxAlive']);
  for (const key of ['duration', 'speed']) if (Object.hasOwn(c, key) && !finite(c[key])) throw new Error(`${label}: clock.${key} must be a number`);
  for (const key of ['loop', 'prewarm']) if (Object.hasOwn(c, key) && typeof c[key] !== 'boolean') throw new Error(`${label}: clock.${key} must be true or false`);
  if ((c.prewarm === true && c.loop !== false) !== Object.hasOwn(c, 'prewarmWindow') || (Object.hasOwn(c, 'prewarmWindow') && !(finite(c.prewarmWindow) && c.prewarmWindow >= 0))) {
    throw new Error(`${label}: clock.prewarmWindow is written exactly when the system prewarms and loops`);
  }
  if (Object.hasOwn(c, 'startDelay') && !(finite(c.startDelay) || (Array.isArray(c.startDelay) && c.startDelay[0] === 'r'))) throw new Error(`${label}: clock.startDelay must be a number or ["r", min, max]`);
  if (Object.hasOwn(c, 'startDelay')) curveShape(c.startDelay, `${label} clock.startDelay`);
  if (Object.hasOwn(c, 'maxParticles') && !(Number.isSafeInteger(c.maxParticles) && c.maxParticles >= 0)) throw new Error(`${label}: clock.maxParticles must be a count`);
  if (Object.hasOwn(c, 'seed') && !(Number.isSafeInteger(c.seed) && c.seed >= 0 && c.seed <= 0xffffffff)) throw new Error(`${label}: clock.seed must be a uint32 (absent: a new seed each play)`);
  if (Object.hasOwn(c, 'space') && !['local', 'world'].includes(c.space)) throw new Error(`${label}: clock.space must be local or world`);
  if (Object.hasOwn(c, 'scaling') && !['hierarchy', 'local', 'shape'].includes(c.scaling)) throw new Error(`${label}: clock.scaling must be hierarchy, local or shape`);
  if (!(Number.isSafeInteger(c.maxAlive) && c.maxAlive >= 1)) throw new Error(`${label}: clock.maxAlive must be a count >= 1`);
  const em = s.emission;
  only(em, `${label} emission`, ['enabled', 'rate', 'distance', 'bursts']);
  if (Object.hasOwn(em, 'enabled') && em.enabled !== false) throw new Error(`${label}: emission.enabled is written only when false`);
  for (const key of ['rate', 'distance']) if (Object.hasOwn(em, key)) curveShape(em[key], `${label} emission.${key}`);
  if (Object.hasOwn(em, 'bursts')) {
    if (!Array.isArray(em.bursts) || !em.bursts.length) throw new Error(`${label}: emission.bursts must be a non-empty list (absent: none)`);
    em.bursts.forEach((b, i) => {
      if (!Array.isArray(b) || b.length !== 5 || !finite(b[0]) || !Number.isSafeInteger(b[2]) || b[2] < 0 || !finite(b[3]) || !finite(b[4])) {
        throw new Error(`${label}: emission.bursts[${i}] must be [time, count, cycles, interval, probability]`);
      }
      curveShape(b[1], `${label} emission.bursts[${i}] count`);
    });
  }
  const st = s.start;
  only(st, `${label} start`, ['lifetime', 'speed', 'size', 'sizeXYZ', 'rotation', 'rotationXYZ', 'color', 'gravity', 'flip']);
  for (const key of ['lifetime', 'speed', 'size', 'rotation', 'gravity']) if (Object.hasOwn(st, key)) curveShape(st[key], `${label} start.${key}`);
  if (Object.hasOwn(st, 'size') && Object.hasOwn(st, 'sizeXYZ')) throw new Error(`${label}: start has size or sizeXYZ, not both`);
  if (Object.hasOwn(st, 'rotation') && Object.hasOwn(st, 'rotationXYZ')) throw new Error(`${label}: start has rotation or rotationXYZ, not both`);
  if (Object.hasOwn(st, 'sizeXYZ')) curves3(st.sizeXYZ, `${label} start.sizeXYZ`);
  if (Object.hasOwn(st, 'rotationXYZ')) curves3(st.rotationXYZ, `${label} start.rotationXYZ`);
  if (Object.hasOwn(st, 'color')) colourShape(st.color, `${label} start.color`);
  if (Object.hasOwn(st, 'flip') && !finite(st.flip)) throw new Error(`${label}: start.flip must be a number`);
  if (Object.hasOwn(s, 'shape')) {
    const sh = s.shape;
    if (!isObject(sh) || !SHAPES.has(sh.type)) throw new Error(`${label}: shape.type must be one of ${[...SHAPES].join(', ')}`);
    only(sh, `${label} shape`, [...SHAPE_COMMON, ...SHAPE_FIELDS[sh.type]], ['type']);
    for (const key of ['radius', 'thickness', 'arc', 'angle', 'length', 'donutRadius', 'randomDir', 'spherical', 'randomPos', 'normalOffset']) {
      if (Object.hasOwn(sh, key) && !finite(sh[key])) throw new Error(`${label}: shape.${key} must be a number`);
    }
    for (const key of ['pos', 'rot', 'scale', 'boxThickness']) if (Object.hasOwn(sh, key) && !vec(sh[key], 3)) throw new Error(`${label}: shape.${key} must be [x, y, z]`);
    for (const key of ['arcMode', 'radiusMode', 'spawn']) if (Object.hasOwn(sh, key)) multiShape(sh[key], `${label} shape.${key}`);
    if (sh.type === 'mesh') {
      if (!index(sh.mesh, counts.meshes)) throw new Error(`${label}: shape.mesh ${sh.mesh} is not in meshes`);
      counts.shapeMeshes.add(sh.mesh);
      if (Object.hasOwn(sh, 'placement') && !['vertex', 'edge', 'triangle'].includes(sh.placement)) throw new Error(`${label}: shape.placement must be vertex, edge or triangle`);
      if (Object.hasOwn(sh, 'useColors') && sh.useColors !== false) throw new Error(`${label}: shape.useColors is written only when false`);
    }
  }
  if (Object.hasOwn(s, 'velocity')) {
    const v = s.velocity;
    only(v, `${label} velocity`, ['linear', 'world', 'orbital', 'offset', 'radial', 'speedModifier']);
    for (const key of ['linear', 'orbital', 'offset']) if (Object.hasOwn(v, key)) curves3(v[key], `${label} velocity.${key}`);
    for (const key of ['radial', 'speedModifier']) if (Object.hasOwn(v, key)) curveShape(v[key], `${label} velocity.${key}`);
    if (Object.hasOwn(v, 'world') && v.world !== true) throw new Error(`${label}: velocity.world is written only when true`);
  }
  if (Object.hasOwn(s, 'limit')) {
    const l = s.limit;
    only(l, `${label} limit`, ['magnitude', 'xyz', 'world', 'dampen'], ['dampen']);
    if (Object.hasOwn(l, 'magnitude') === Object.hasOwn(l, 'xyz')) throw new Error(`${label}: limit has magnitude or xyz`);
    if (Object.hasOwn(l, 'magnitude')) curveShape(l.magnitude, `${label} limit.magnitude`);
    if (Object.hasOwn(l, 'xyz')) curves3(l.xyz, `${label} limit.xyz`);
    if (Object.hasOwn(l, 'world') && !(l.world === true && Object.hasOwn(l, 'xyz'))) throw new Error(`${label}: limit.world is written only when true, with xyz`);
    if (!finite(l.dampen)) throw new Error(`${label}: limit.dampen must be a number`);
  }
  if (Object.hasOwn(s, 'force')) {
    only(s.force, `${label} force`, ['xyz', 'world'], ['xyz']);
    curves3(s.force.xyz, `${label} force.xyz`);
    if (Object.hasOwn(s.force, 'world') && s.force.world !== true) throw new Error(`${label}: force.world is written only when true`);
  }
  if (Object.hasOwn(s, 'noise')) {
    const n = s.noise;
    only(n, `${label} noise`, ['strength', 'strengthXYZ', 'frequency', 'damping', 'octaves', 'octaveMultiplier', 'octaveScale', 'quality', 'scroll', 'position', 'rotation', 'size']);
    if (Object.hasOwn(n, 'strength') === Object.hasOwn(n, 'strengthXYZ')) throw new Error(`${label}: noise has strength or strengthXYZ`);
    if (Object.hasOwn(n, 'strength')) curveShape(n.strength, `${label} noise.strength`);
    if (Object.hasOwn(n, 'strengthXYZ')) curves3(n.strengthXYZ, `${label} noise.strengthXYZ`);
    for (const key of ['scroll', 'position', 'rotation', 'size']) if (Object.hasOwn(n, key)) curveShape(n[key], `${label} noise.${key}`);
    for (const key of ['frequency', 'octaveMultiplier', 'octaveScale']) if (Object.hasOwn(n, key) && !finite(n[key])) throw new Error(`${label}: noise.${key} must be a number`);
    if (Object.hasOwn(n, 'octaves') && !(Number.isSafeInteger(n.octaves) && n.octaves >= 1)) throw new Error(`${label}: noise.octaves must be a count`);
    if (Object.hasOwn(n, 'damping') && n.damping !== false) throw new Error(`${label}: noise.damping is written only when false`);
    if (Object.hasOwn(n, 'quality') && !['low', 'medium'].includes(n.quality)) throw new Error(`${label}: noise.quality is low or medium (absent: high)`);
  }
  if (Object.hasOwn(s, 'inherit')) {
    only(s.inherit, `${label} inherit`, ['mode', 'curve'], ['curve']);
    if (Object.hasOwn(s.inherit, 'mode') && s.inherit.mode !== 'current') throw new Error(`${label}: inherit.mode is written only as current`);
    curveShape(s.inherit.curve, `${label} inherit.curve`);
  }
  for (const key of ['size', 'rotation']) {
    if (!Object.hasOwn(s, key)) continue;
    const m = s[key];
    if (!isObject(m) || Object.keys(m).length !== 1 || !(Object.hasOwn(m, 'curve') || Object.hasOwn(m, 'xyz'))) throw new Error(`${label}: ${key} must be { curve } or { xyz }`);
    if (Object.hasOwn(m, 'curve')) curveShape(m.curve, `${label} ${key}.curve`); else curves3(m.xyz, `${label} ${key}.xyz`);
  }
  if (Object.hasOwn(s, 'color')) {
    only(s.color, `${label} color`, ['gradient'], ['gradient']);
    colourShape(s.color.gradient, `${label} color.gradient`);
  }
  if (Object.hasOwn(s, 'sheet')) {
    const m = s.sheet;
    only(m, `${label} sheet`, ['tiles', 'animation', 'row', 'frameOverTime', 'startFrame', 'cycles'], ['tiles', 'frameOverTime']);
    if (!Array.isArray(m.tiles) || m.tiles.length !== 2 || !m.tiles.every((t) => Number.isSafeInteger(t) && t >= 1)) throw new Error(`${label}: sheet.tiles must be [x, y] counts`);
    if (Object.hasOwn(m, 'animation') && m.animation !== 'singleRow') throw new Error(`${label}: sheet.animation is written only as singleRow`);
    if ((m.animation === 'singleRow') !== Object.hasOwn(m, 'row') || (Object.hasOwn(m, 'row') && !(m.row === 'random' || index(m.row, m.tiles[1])))) {
      throw new Error(`${label}: sheet.row is random or a row index, with singleRow only`);
    }
    curveShape(m.frameOverTime, `${label} sheet.frameOverTime`);
    if (Object.hasOwn(m, 'startFrame')) curveShape(m.startFrame, `${label} sheet.startFrame`);
    if (Object.hasOwn(m, 'cycles') && !finite(m.cycles)) throw new Error(`${label}: sheet.cycles must be a number`);
  }
  if (Object.hasOwn(s, 'custom')) {
    if (!Array.isArray(s.custom) || s.custom.length !== 2) throw new Error(`${label}: custom must be [slot 1, slot 2]`);
    s.custom.forEach((slot, i) => {
      if (slot === null) return;
      if (isObject(slot) && Object.keys(slot).length === 1 && Array.isArray(slot.vector) && slot.vector.length >= 1 && slot.vector.length <= 4) {
        slot.vector.forEach((v, k) => curveShape(v, `${label} custom[${i}].vector[${k}]`));
      } else if (isObject(slot) && Object.keys(slot).length === 1 && Object.hasOwn(slot, 'color')) {
        colourShape(slot.color, `${label} custom[${i}].color`);
      } else {
        throw new Error(`${label}: custom[${i}] must be null, { vector: 1-4 curves } or { color }`);
      }
    });
  }
  if (Object.hasOwn(s, 'sub')) {
    if (!Array.isArray(s.sub) || !s.sub.length) throw new Error(`${label}: sub must list [child, birth | death, probability]`);
    s.sub.forEach((link, i) => {
      if (!Array.isArray(link) || link.length !== 3 || !index(link[0], counts.systems) || !['birth', 'death'].includes(link[1]) || !finite(link[2])) {
        throw new Error(`${label}: sub[${i}] must be [child system, birth | death, probability]`);
      }
    });
  }
  const r = s.render;
  if (r === null) {
    if (s.material !== null) throw new Error(`${label}: a system that draws nothing (render null) has no material`);
  } else {
    only(r, `${label} render`, ['mode', 'align', 'pivot', 'sort', 'lengthScale', 'velocityScale', 'freeform', 'minSize', 'maxSize', 'meshes', 'allowRoll'], ['mode']);
    if (!RENDER_MODES.has(r.mode)) throw new Error(`${label}: render.mode must be ${[...RENDER_MODES].join(', ')}`);
    if (Object.hasOwn(r, 'align') && !ALIGNMENTS.has(r.align)) throw new Error(`${label}: render.align must be ${[...ALIGNMENTS].join(', ')}`);
    if (Object.hasOwn(r, 'sort') && !SORT_MODES.has(r.sort)) throw new Error(`${label}: render.sort must be ${[...SORT_MODES].join(', ')}`);
    if (Object.hasOwn(r, 'pivot') && !vec(r.pivot, 3)) throw new Error(`${label}: render.pivot must be [x, y, z]`);
    for (const key of ['lengthScale', 'velocityScale', 'minSize', 'maxSize']) if (Object.hasOwn(r, key) && !finite(r[key])) throw new Error(`${label}: render.${key} must be a number`);
    for (const key of ['lengthScale', 'velocityScale', 'freeform']) if (Object.hasOwn(r, key) && r.mode !== 'stretched') throw new Error(`${label}: render.${key} is for stretched particles`);
    if (Object.hasOwn(r, 'freeform') && r.freeform !== true) throw new Error(`${label}: render.freeform is written only when true`);
    if (Object.hasOwn(r, 'allowRoll') && r.allowRoll !== false) throw new Error(`${label}: render.allowRoll is written only when false`);
    if ((r.mode === 'mesh') !== Object.hasOwn(r, 'meshes')) throw new Error(`${label}: render.meshes exactly for mesh particles`);
    if (Object.hasOwn(r, 'meshes')) {
      if (!Array.isArray(r.meshes) || !r.meshes.length || r.meshes.length > 4 || !r.meshes.every((m) => index(m, counts.meshes))) throw new Error(`${label}: render.meshes must be 1-4 indices into meshes`);
      for (const m of r.meshes) counts.renderMeshes.add(m);
    }
    if (!index(s.material, counts.materials)) throw new Error(`${label}: material ${s.material} is not in materials`);
    counts.usedMaterials.add(s.material);
  }
}

/** A mesh: { builtin: "quad" } or its geometry (normals and areaCdf only on a mesh a shape emits from). */
function meshShape(m, label) {
  if (isObject(m) && Object.keys(m).length === 1 && m.builtin === 'quad') return;
  only(m, label, ['vertices', 'uvs', 'colors', 'triangles', 'normals', 'areaCdf'], ['vertices', 'uvs', 'colors', 'triangles', 'normals', 'areaCdf']);
  if (!Array.isArray(m.vertices) || !m.vertices.length || m.vertices.length % 3 || !m.vertices.every(finite)) throw new Error(`${label}: vertices must be x, y, z triples`);
  const count = m.vertices.length / 3;
  if (count > 65535) throw new Error(`${label}: ${count} vertices (16-bit indices)`);
  if (!Array.isArray(m.uvs) || m.uvs.length !== count * 2 || !m.uvs.every(finite)) throw new Error(`${label}: uvs must pair with vertices`);
  if (m.colors !== null && (!Array.isArray(m.colors) || m.colors.length !== count * 4 || !m.colors.every(finite))) throw new Error(`${label}: colors must be null or r, g, b, a per vertex`);
  if (!Array.isArray(m.triangles) || !m.triangles.length || m.triangles.length % 3 || !m.triangles.every((t) => index(t, count))) throw new Error(`${label}: triangles must index the vertices`);
  if ((m.normals === null) !== (m.areaCdf === null)) throw new Error(`${label}: normals and areaCdf come together (a mesh shape's)`);
  if (m.normals !== null && (!Array.isArray(m.normals) || m.normals.length !== count * 3 || !m.normals.every(finite))) throw new Error(`${label}: normals must be x, y, z per vertex`);
  if (m.areaCdf !== null) {
    const cdf = m.areaCdf;
    if (!Array.isArray(cdf) || cdf.length !== m.triangles.length / 3 || !cdf.every(finite) || cdf.some((v, i) => v < (i ? cdf[i - 1] : 0)) || cdf.at(-1) !== 1) {
      throw new Error(`${label}: areaCdf must rise to 1, one value per triangle`);
    }
  }
}

/** A material: { blend, texture, color, shader, custom, cull }; returns the textures it samples. */
function materialShape(m, label, textureCount) {
  only(m, label, ['blend', 'texture', 'color', 'shader', 'custom', 'cull'], ['blend', 'texture', 'color', 'shader', 'custom', 'cull']);
  if (!['alpha', 'add'].includes(m.blend)) throw new Error(`${label}: blend must be alpha or add`);
  if (m.texture !== null && !index(m.texture, textureCount)) throw new Error(`${label}: texture ${m.texture} is not a texture`);
  if (!vec(m.color, 4) || !m.color.every((c) => c >= 0)) throw new Error(`${label}: color must be [r, g, b, a]`);
  const used = shaderShape(m.shader, `${label} shader`, textureCount);
  if (m.shader.animated.length) throw new Error(`${label}: a particle material's shader lists no animated parameter (timelines carry them)`);
  if (m.custom !== null && (!isObject(m.custom) || Object.keys(m.custom).length !== 1 || !Array.isArray(m.custom.inputs) || m.custom.inputs.length !== 8
      || !m.custom.inputs.every((source) => source === 0 || (typeof source === 'string' && INPUT.test(source))))) {
    throw new Error(`${label}: custom must be null or { inputs: 8 sources (uv.x, uv2.y, c1.x ... c2.w, or 0) }`);
  }
  if (![0, 1, 2].includes(m.cull)) throw new Error(`${label}: cull must be 0, 1 (front) or 2 (back)`);
  return m.texture === null ? used : [m.texture, ...used];
}

/**
 * The shape of a layerParticles.json: `first` is the number of textures layers.json lists (textures and
 * effectTextures), the index the particle textures continue from. Returns the particle texture records.
 */
export function particlesShape(doc, label, first) {
  only(doc, label, ['version', 'unit', 'camera', 'controller', 'textures', 'materials', 'meshes', 'systems', 'trails'],
    ['version', 'unit', 'camera', 'controller', 'textures', 'materials', 'meshes', 'systems']);
  // TrailRenderer components (written with trail data, from the second export on; nothing draws them yet).
  if (Object.hasOwn(doc, 'trails') && (!Array.isArray(doc.trails) || !doc.trails.every(isObject))) throw new Error(`${label}: trails must be a list of records`);
  if (doc.version !== PARTICLES_VERSION) throw new Error(`${label}: version must be ${PARTICLES_VERSION}`);
  if (!(finite(doc.unit) && doc.unit > 0)) throw new Error(`${label}: unit must be > 0`);
  only(doc.camera, `${label} camera`, ['size', 'height'], ['size', 'height']);
  if (!finite(doc.camera.size) || !finite(doc.camera.height) || doc.camera.size < 0 || doc.camera.height < 0) throw new Error(`${label}: camera size and height must be numbers >= 0`);
  only(doc.controller, `${label} controller`, ['fixFxDelay', 'animTimeFixed'], ['fixFxDelay', 'animTimeFixed']);
  if (!Number.isSafeInteger(doc.controller.fixFxDelay) || !Number.isSafeInteger(doc.controller.animTimeFixed)) throw new Error(`${label}: controller flags must be integers`);
  if (!Array.isArray(doc.textures) || !Array.isArray(doc.materials) || !Array.isArray(doc.meshes) || !Array.isArray(doc.systems) || !doc.systems.length) {
    throw new Error(`${label}: textures, materials, meshes and systems must be lists (systems not empty)`);
  }
  if (doc.textures.length > PARTICLE_LIMITS.textures || doc.systems.length > PARTICLE_LIMITS.systems) {
    throw new Error(`${label}: ${doc.textures.length} textures and ${doc.systems.length} systems (at most ${PARTICLE_LIMITS.textures} and ${PARTICLE_LIMITS.systems})`);
  }
  doc.textures.forEach((t, i) => {
    if (!isObject(t) || t.file !== `layer${first + i}.webp`) throw new Error(`${label}: textures[${i}] must be layer${first + i}.webp`);
    if (!PHASES.includes(t.phase)) throw new Error(`${label}: textures[${i}] phase must be ${PHASES.join(', ')}`);
  });
  const textureCount = first + doc.textures.length;
  const sampled = new Set();
  doc.materials.forEach((m, i) => { for (const t of materialShape(m, `${label} materials[${i}]`, textureCount)) sampled.add(t); });
  doc.meshes.forEach((m, i) => meshShape(m, `${label} meshes[${i}]`));
  const counts = { systems: doc.systems.length, materials: doc.materials.length, meshes: doc.meshes.length, usedMaterials: new Set(), renderMeshes: new Set(), shapeMeshes: new Set() };
  doc.systems.forEach((s, i) => systemShape(s, `${label} systems[${i}]`, counts));
  doc.textures.forEach((t, i) => { if (!sampled.has(first + i)) throw new Error(`${label}: ${t.file} is not sampled by any particle material`); });
  doc.materials.forEach((_, i) => { if (!counts.usedMaterials.has(i)) throw new Error(`${label}: materials[${i}] is used by no system`); });
  doc.meshes.forEach((m, i) => {
    if (!counts.renderMeshes.has(i) && !counts.shapeMeshes.has(i)) throw new Error(`${label}: meshes[${i}] is used by no system`);
    if (counts.shapeMeshes.has(i) !== (m.areaCdf !== null && m.areaCdf !== undefined)) throw new Error(`${label}: meshes[${i}] has normals and areaCdf exactly when a shape emits from it`);
  });
  return doc.textures;
}

/** The {particles} runs of layers.json's draw list against the systems: each drawn system in exactly one run, in order. */
export function runsShape(draw, systems, label) {
  const order = [];
  draw.forEach((entry, i) => {
    if (!Object.hasOwn(entry, 'particles')) return;
    if (i > 0 && Object.hasOwn(draw[i - 1], 'particles')) throw new Error(`${label}: draw[${i}] continues the run before it (runs are maximal)`);
    order.push(...entry.particles);
  });
  const drawn = systems.flatMap((s, i) => (s.render === null ? [] : [i]));
  if (order.length !== drawn.length || order.some((v, i) => v !== drawn[i])) throw new Error(`${label}: the particle runs must list every drawn system once, in order`);
}
