// layers.json: the illustration prefab's own mesh layers (scripts/layers.py), checked and framed.
// Used by scripts/spine.mjs (the sync's bounds, re-read by the validator) and scripts/manifest.mjs.

const BLENDS = new Set(['alpha', 'add']);
const ONLY = new Set(['Idle', 'Interact', 'Special', 'Start']);
const WRAPS = new Set(['repeat', 'clamp', 'mirror']);
const OMITTED_COUNTS = ['particles', 'trails', 'skinned', 'hidden', 'holders'];
const OMITTED_LISTS = ['custom', 'externalTexture', 'other'];
const FRAME_LENGTH = 16; // [t, a, b, c, d, tx, ty, r, g, b, alpha, active, su, ou, sv, ov]
// A tilted entry's frames also carry the depth column [e, f] of their 2x4 projection, at 16 and 17.
const SOLID_FRAME_LENGTH = 18;
// Vertices further than this from the skeleton's origin (skeleton units) mean a unit went wrong.
const REACH = 100000;

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const index = (value, length) => Number.isSafeInteger(value) && value >= 0 && value < length;
const vec = (value, length) => Array.isArray(value) && value.length === length && value.every(finite);
const keys = (value, wanted) => isObject(value) && Object.keys(value).length === wanted.length && wanted.every((key) => Object.hasOwn(value, key));
// The layers.json format model.json's layersVersion names (scripts/layers.py LAYERS_VERSION): 1, plain
// layers only; 2, also effect entries, a plain layer's exact, effectTextures and effectBounds; 3, also
// tilted entries (3D vertices and a 2x4 orthographic projection per frame), integrated scroll offsets
// and Disturb2's animated parameters.
export const LAYERS_VERSION = 3;
const SHADER_FAMILIES = new Set(['particle', 'noise']);
const NOISE_MODES = new Set(['default', 'add', 'glow']);
const MAP_KEYS = ['texture', 'st', 'speed', 'scroll'];

/** A texture map of an effect: { texture, st, speed, scroll } plus `extra` members; returns its texture (or null). */
function shaderMap(map, label, count, extra = [], nullable = false) {
  if (!keys(map, [...MAP_KEYS, ...extra])) throw new Error(`${label} must be { ${[...MAP_KEYS, ...extra].join(', ')} }`);
  if (!(map.texture === null && nullable) && !index(map.texture, count)) throw new Error(`${label}: texture ${map.texture} is not in textures`);
  if (!vec(map.st, 4) || !vec(map.speed, 2) || !vec(map.scroll, 2)) throw new Error(`${label}: st [sx, sy, ox, oy], speed [u, v] and scroll [u, v] must be numbers`);
  return map.texture;
}

/**
 * How many numbers an effect's animated parameters add to each of its frames (scripts/effects.py
 * parameters / width), checking every path names a member the shader has, once.
 */
export function animatedWidth(shader, label) {
  if (!Array.isArray(shader.animated) || new Set(shader.animated).size !== shader.animated.length) throw new Error(`${label}: animated must be distinct parameter paths`);
  let width = 0;
  for (const path of shader.animated) {
    const p = typeof path === 'string' ? path.split('.') : [];
    const d = shader.distort;
    let ok;
    if (shader.family === 'noise') {
      // Disturb2: its noise params, its glow colour, and the integrated scroll of its two noise channels.
      ok = ['noise1', 'noise2', 'noise.offset'].includes(path) || (path === 'glow' && shader.glow !== null);
    } else {
      ok = (p[0] === 'dissolve' && p.length === 3 && index(Number(p[1]), shader.dissolve?.length ?? 0) && ['amount', 'border', 'st', 'offset'].includes(p[2]))
        || (p[0] === 'distort' && d && p[1] === 'maps' && p.length === 4 && index(Number(p[2]), d.maps.length)
          && (p[3] === 'intensity' || (['st', 'offset'].includes(p[3]) && d.maps[Number(p[2])].texture !== null)))
        || (['distort.main', 'distort.dissolve'].includes(path) && d) || path === 'main.offset'
        || (path === 'distort.weight.st' && d && d.weight) || (path === 'ramp.st' && shader.ramp)
        || (['vertex.st', 'vertex.intensity', 'vertex.offset'].includes(path) && shader.vertex)
        || (path === 'vertex.weight.st' && shader.vertex?.weight) || (path === 'edge.color' && shader.edge);
    }
    if (!ok) throw new Error(`${label}: animated ${path} is not a parameter of this shader`);
    width += path === 'vertex.intensity' ? 3 : { amount: 1, border: 1, st: 4, intensity: 2, color: 4, offset: 4, main: 1, dissolve: 1, noise1: 4, noise2: 4, glow: 4 }[p.at(-1)];
  }
  return width;
}

/**
 * A layer's `shader` (scripts/effects.py): its family and every member, each texture in range.
 * Returns the texture indices it samples besides the main texture.
 */
export function shaderShape(shader, label, count) {
  if (!isObject(shader) || !SHADER_FAMILIES.has(shader.family)) throw new Error(`${label}: shader family must be ${[...SHADER_FAMILIES].join(' or ')}`);
  const main = shader.main;
  if (!keys(main, ['st', 'speed', 'scroll', 'fract']) || !vec(main.st, 4) || !vec(main.speed, 2) || !vec(main.scroll, 2) || typeof main.fract !== 'boolean') {
    throw new Error(`${label}: shader main must be { st, speed, scroll, fract }`);
  }
  const used = [];
  if (shader.family === 'noise') {
    if (!keys(shader, ['family', 'mode', 'main', 'noise', 'noise1', 'noise2', 'glow', 'animated']) || !NOISE_MODES.has(shader.mode)) throw new Error(`${label}: a noise shader is { family, mode, main, noise, noise1, noise2, glow, animated }`);
    used.push(shaderMap(shader.noise, `${label} noise`, count));
    if (!vec(shader.noise1, 4) || !vec(shader.noise2, 4)) throw new Error(`${label}: noise1 and noise2 must be [scale, speed, x, y]`);
    if ((shader.mode === 'glow') !== (shader.glow !== null) || (shader.glow !== null && !vec(shader.glow, 4))) throw new Error(`${label}: glow must be [r, g, b, a] in glow mode, else null`);
    animatedWidth(shader, label);
    return used;
  }
  if (!keys(shader, ['family', 'main', 'distort', 'dissolve', 'edge', 'ramp', 'vertex', 'animated'])) throw new Error(`${label}: a particle shader is { family, main, distort, dissolve, edge, ramp, vertex, animated }`);
  const d = shader.distort;
  if (d !== null) {
    if (!keys(d, ['space', 'main', 'dissolve', 'constant', 'maps', 'weight']) || !['main', 'raw'].includes(d.space) || !finite(d.main) || !finite(d.dissolve)
        || !vec(d.constant, 2) || !Array.isArray(d.maps) || d.maps.length > 2) {
      throw new Error(`${label}: distort must be { space: main | raw, main, dissolve, constant, maps (up to 2), weight }`);
    }
    if (!d.maps.length && !d.constant.some((v) => v !== 0)) throw new Error(`${label}: a distortion moves nothing`);
    d.maps.forEach((map, i) => {
      used.push(shaderMap(map, `${label} distort.maps[${i}]`, count, ['anchor', 'intensity'], true));
      if (!vec(map.anchor, 2) || !vec(map.intensity, 2)) throw new Error(`${label}: distort.maps[${i}] anchor and intensity must be [u, v]`);
    });
    if (d.weight !== null) used.push(shaderMap(d.weight, `${label} distort.weight`, count));
  }
  if (!Array.isArray(shader.dissolve) || shader.dissolve.length > 2) throw new Error(`${label}: dissolve must be a list of up to 2`);
  shader.dissolve.forEach((map, i) => {
    used.push(shaderMap(map, `${label} dissolve[${i}]`, count, ['fract', 'amount', 'border']));
    if (typeof map.fract !== 'boolean' || !finite(map.amount) || !finite(map.border) || !(map.border > 0)) throw new Error(`${label}: dissolve[${i}] needs fract, amount and a border > 0`);
  });
  if (shader.edge !== null && (!keys(shader.edge, ['color', 'pow', 'epsilon']) || !vec(shader.edge.color, 4) || !finite(shader.edge.pow) || typeof shader.edge.epsilon !== 'boolean')) {
    throw new Error(`${label}: edge must be null or { color, pow, epsilon }`);
  }
  if (shader.edge !== null && !shader.dissolve.length) throw new Error(`${label}: an edge needs a dissolve`);
  if (shader.ramp !== null) used.push(shaderMap(shader.ramp, `${label} ramp`, count));
  const v = shader.vertex;
  if (v !== null) {
    used.push(shaderMap(v, `${label} vertex`, count, ['intensity', 'weight', 'matrix']));
    if (!vec(v.intensity, 3) || !vec(v.matrix, 6)) throw new Error(`${label}: vertex intensity must be [x, y, z] and matrix 6 numbers`);
    if (v.weight !== null) used.push(shaderMap(v.weight, `${label} vertex.weight`, count));
  }
  animatedWidth(shader, label);
  return used.filter((t) => t !== null);
}

function timeline(value, label, width = FRAME_LENGTH) {
  if (!isObject(value) || !finite(value.length) || value.length < 0 || typeof value.loop !== 'boolean' || !finite(value.loopFrom)
      || value.loopFrom < 0 || (value.loop && value.length > 0 && value.loopFrom >= value.length) || !Array.isArray(value.frames) || !value.frames.length) {
    throw new Error(`${label}: a timeline must be { length >= 0, loop, loopFrom, frames }`);
  }
  let last = -Infinity;
  value.frames.forEach((frame, i) => {
    if (!Array.isArray(frame) || frame.length !== width || !frame.every(finite)) throw new Error(`${label}: frames[${i}] must be ${width} numbers`);
    if (!(frame[0] > last) || frame[0] > value.length + 1e-3) throw new Error(`${label}: frames[${i}] is out of order or past the timeline (${frame[0]}s)`);
    if (frame[11] !== 0 && frame[11] !== 1) throw new Error(`${label}: frames[${i}] active must be 0 or 1`);
    last = frame[0];
  });
  if (value.frames[0][0] !== 0) throw new Error(`${label}: frames must start at 0`);
}

/**
 * The shape of a layers.json: every field, every index in range, every texture named
 * layer<N>.webp in order. Returns the texture records. Slot and bone names are checked against the
 * skeleton by layerBounds (the Spine runtime has them).
 */
export function layersShape(doc, label, version = LAYERS_VERSION) {
  if (!isObject(doc) || doc.schemaVersion !== 1) throw new Error(`${label}: layers.json schemaVersion must be 1`);
  if (!Array.isArray(doc.textures)) throw new Error(`${label}: layers.json textures missing`);
  if (version >= 2 && (!Array.isArray(doc.effectTextures) || !Object.hasOwn(doc, 'effectBounds'))) {
    throw new Error(`${label}: layers.json of layersVersion ${version} must have effectTextures and effectBounds`);
  }
  if (version < 2 && (Object.hasOwn(doc, 'effectTextures') || Object.hasOwn(doc, 'effectBounds'))) {
    throw new Error(`${label}: layers.json has effects, but model.json says layersVersion ${version}`);
  }
  // Plain layers' textures (every reader fetches them), then those only effects sample, numbered on.
  const all = [...doc.textures, ...(doc.effectTextures ?? [])];
  const plainCount = doc.textures.length;
  all.forEach((texture, i) => {
    if (!isObject(texture) || texture.file !== `layer${i}.webp`) throw new Error(`${label}: layers textures[${i}] must be layer${i}.webp`);
    if (!Number.isSafeInteger(texture.width) || texture.width <= 0 || !Number.isSafeInteger(texture.height) || texture.height <= 0) throw new Error(`${label}: layers textures[${i}] width/height missing`);
    if (!Number.isSafeInteger(texture.bytes) || texture.bytes <= 0 || typeof texture.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(texture.sha256)) throw new Error(`${label}: layers textures[${i}] bytes/sha256 missing`);
    if (!Array.isArray(texture.wrap) || texture.wrap.length !== 2 || !texture.wrap.every((w) => WRAPS.has(w))) throw new Error(`${label}: layers textures[${i}] wrap must be two of ${[...WRAPS].join(', ')}`);
    const o = texture.opaque;
    if (!Array.isArray(o) || o.length !== 4 || !o.every((v) => finite(v) && v >= 0 && v <= 1) || !(o[0] < o[2] && o[1] < o[3])) {
      throw new Error(`${label}: layers textures[${i}] opaque must be [u0, v0, u1, v1] in 0-1, the part of the texture that shows`);
    }
  });
  for (const key of version >= 2 ? ['bounds', 'effectBounds'] : ['bounds']) {
    const bounds = doc[key];
    if (!isObject(bounds) || !['x', 'y', 'width', 'height'].every((k) => finite(bounds[k])) || bounds.width <= 0 || bounds.height <= 0) throw new Error(`${label}: layers ${key} missing`);
  }
  if (!Array.isArray(doc.draw) || !doc.draw.length) throw new Error(`${label}: layers draw missing`);
  if (!Array.isArray(doc.separators) || !doc.separators.every((name) => typeof name === 'string' && name) || new Set(doc.separators).size !== doc.separators.length) {
    throw new Error(`${label}: layers separators must be distinct slot names`);
  }
  const used = new Set();
  const usedByEffects = new Set();
  const parts = new Set();
  doc.draw.forEach((entry, i) => {
    const at = `${label}: layers draw[${i}]`;
    if (isObject(entry) && Object.hasOwn(entry, 'part') && Object.keys(entry).length === 1) {
      // Part k: the slots from the k-th separator met in the draw order to the next.
      if (!Number.isSafeInteger(entry.part) || entry.part < 0 || entry.part > doc.separators.length || parts.has(entry.part)) {
        throw new Error(`${at}: part must be a distinct index 0-${doc.separators.length}`);
      }
      parts.add(entry.part);
      return;
    }
    // A tilted entry is an effect entry whose mesh turns in depth as it moves: 3D vertices, and frames
    // that carry the depth column of their projection.
    const solid = isObject(entry) && Object.keys(entry).length === 1 && isObject(entry.tilted);
    const effect = (isObject(entry) && Object.keys(entry).length === 1 && isObject(entry.effect)) || solid;
    if (!isObject(entry) || Object.keys(entry).length !== 1 || !(isObject(entry.layer) || (isObject(entry.effect) && version >= 2) || (solid && version >= 3))) {
      throw new Error(`${at}: must be ${version >= 3 ? '{ part }, { layer }, { effect } or { tilted }' : version >= 2 ? '{ part }, { layer } or { effect }' : '{ part } or { layer }'}`);
    }
    const layer = solid ? entry.tilted : effect ? entry.effect : entry.layer;
    if (typeof layer.name !== 'string') throw new Error(`${at}: name missing`);
    if (!BLENDS.has(layer.blend)) throw new Error(`${at}: blend must be alpha or add`);
    if (effect) {
      // An effect's main texture may be any (null: Unity's white, the material binds none); a plain
      // layer's must be one every reader fetches.
      if (layer.texture !== null && !index(layer.texture, all.length)) throw new Error(`${at}: texture ${layer.texture} is not in textures`);
      if (layer.texture !== null) usedByEffects.add(layer.texture);
      for (const t of shaderShape(layer.shader, `${at} shader`, all.length)) usedByEffects.add(t);
      for (const key of ['scroll', 'approximated', 'exact']) if (Object.hasOwn(layer, key)) throw new Error(`${at}: an effect has no ${key} (its shader scrolls)`);
      // Faces culled as it is drawn (Unity's front faces wind clockwise): only a layer that moves; a
      // static one's culled faces are already gone.
      if (![0, 1, 2].includes(layer.cull) || (layer.cull && layer.animation === null && layer.follow === null)) throw new Error(`${at}: cull must be 0, or 1 (front) or 2 (back) for a layer that moves`);
      // Where it shows at its first frame (in its own UVs), or null.
      if (!(layer.visible === null || (vec(layer.visible, 4) && layer.visible[0] < layer.visible[2] && layer.visible[1] < layer.visible[3]))) {
        throw new Error(`${at}: visible must be null or [u0, v0, u1, v1]`);
      }
    } else {
      if (!index(layer.texture, plainCount)) throw new Error(`${at}: texture ${layer.texture} is not in textures`);
      used.add(layer.texture);
      if (Object.hasOwn(layer, 'shader')) throw new Error(`${at}: a plain layer has no shader (an exact one goes in exact)`);
    }
    const { vertices, uvs, colors, triangles } = layer;
    const stride = solid ? 3 : 2;
    if (!Array.isArray(vertices) || vertices.length < 3 * stride || vertices.length % stride || !vertices.every(finite)) {
      throw new Error(`${at}: vertices must be ${solid ? 'x, y, z triples' : 'x, y pairs'}`);
    }
    const count = vertices.length / stride;
    if (count > 65535) throw new Error(`${at}: ${count} vertices (16-bit indices)`);
    if (!Array.isArray(uvs) || uvs.length !== count * 2 || !uvs.every(finite)) throw new Error(`${at}: uvs must pair with vertices`);
    if (colors !== null && (!Array.isArray(colors) || colors.length !== count * 4 || !colors.every((c) => finite(c) && c >= 0 && c <= 1))) throw new Error(`${at}: colors must be null or r, g, b, a per vertex in 0-1`);
    if (!Array.isArray(triangles) || !triangles.length || triangles.length % 3 || !triangles.every((t) => index(t, count))) throw new Error(`${at}: triangles must index the vertices`);
    // An effect's animated parameters ride on its frames, past the 16 every layer has (18 for a tilted one).
    const width = (solid ? SOLID_FRAME_LENGTH : FRAME_LENGTH) + (effect ? animatedWidth(layer.shader, `${at} shader`) : 0);
    if (solid && layer.animation === null) throw new Error(`${at}: a tilted entry moves (a still one is flattened into an effect)`);
    if (layer.animation === null) {
      if (!Array.isArray(layer.color) || layer.color.length !== 4 || !layer.color.every((c) => finite(c) && c >= 0)) throw new Error(`${at}: color must be [r, g, b, a]`);
      if (width !== FRAME_LENGTH) throw new Error(`${at}: a layer without a timeline animates no parameter`);
    } else {
      if (layer.color !== null) throw new Error(`${at}: an animated layer takes its colour from its frames`);
      timeline(layer.animation, `${at} animation`, width);
      if (layer.animation.states !== undefined) {
        if (!isObject(layer.animation.states) || !Object.keys(layer.animation.states).length) throw new Error(`${at}: animation.states must name animations`);
        for (const [name, state] of Object.entries(layer.animation.states)) {
          if (!ONLY.has(name) || name === 'Idle') throw new Error(`${at}: animation.states.${name} is not a triggered animation`);
          timeline(state, `${at} animation.states.${name}`, width);
        }
      }
    }
    if (layer.follow !== null) {
      const f = layer.follow;
      if (!isObject(f) || typeof f.bone !== 'string' || !f.bone || !['xy', 'rotation', 'localScale', 'mirrored'].every((k) => typeof f[k] === 'boolean')
          || !Array.isArray(f.parent) || f.parent.length !== 4 || !f.parent.every(finite) || !Array.isArray(f.position) || f.position.length !== 2
          || !f.position.every(finite) || !finite(f.angle)) {
        throw new Error(`${at}: follow must be { bone, xy, rotation, localScale, mirrored, parent: [a, b, c, d], position: [x, y], angle }`);
      }
    }
    if (!effect && layer.scroll !== null && (!Array.isArray(layer.scroll) || layer.scroll.length !== 2 || !layer.scroll.every(finite))) throw new Error(`${at}: scroll must be null or [u, v] per second`);
    if (layer.only !== null && !ONLY.has(layer.only)) throw new Error(`${at}: only must be null or ${[...ONLY].join(', ')}`);
    if (!finite(layer.delay) || layer.delay < 0) throw new Error(`${at}: delay must be seconds >= 0`);
    // What the site draws differently from the game (scripts/layers.py FLOW_UNDISTORTED), or null.
    if (!effect && layer.approximated !== null && (typeof layer.approximated !== 'string' || !layer.approximated.trim())) throw new Error(`${at}: approximated must be null or a note`);
    if (!effect && Object.hasOwn(layer, 'exact')) {
      // The same layer drawn exactly by readers that know its effect: the mesh's own UVs and the shader.
      if (version < 2) throw new Error(`${at}: exact needs layersVersion 2`);
      if (!layer.approximated) throw new Error(`${at}: only an approximated layer has an exact effect`);
      if (!keys(layer.exact, ['uvs', 'shader']) || !Array.isArray(layer.exact.uvs) || layer.exact.uvs.length !== vertices.length || !layer.exact.uvs.every(finite)) {
        throw new Error(`${at}: exact must be { uvs (paired with vertices), shader }`);
      }
      for (const t of shaderShape(layer.exact.shader, `${at} exact.shader`, all.length)) usedByEffects.add(t);
      if (layer.exact.shader.animated.length) throw new Error(`${at}: an exact effect animates no parameter (a plain layer's frames have 16 numbers)`);
    }
    if (layer.follow === null && layer.animation === null && !vertices.every((v) => Math.abs(v) < REACH)) throw new Error(`${at}: vertices out of reach (${REACH} skeleton units)`);
  });
  if (!parts.size) throw new Error(`${label}: layers draw has no skeleton part`);
  doc.textures.forEach((texture, i) => { if (!used.has(i)) throw new Error(`${label}: layers ${texture.file} is not drawn by any plain layer`); });
  (doc.effectTextures ?? []).forEach((texture, i) => {
    if (!usedByEffects.has(plainCount + i)) throw new Error(`${label}: layers ${texture.file} is not sampled by any effect`);
  });
  const omitted = doc.omitted;
  if (!isObject(omitted) || !OMITTED_COUNTS.every((k) => Number.isSafeInteger(omitted[k]) && omitted[k] >= 0)
      || !OMITTED_LISTS.every((k) => Array.isArray(omitted[k]) && omitted[k].every((item) => isObject(item) && typeof item.name === 'string' && typeof item.reason === 'string' && item.reason))) {
    throw new Error(`${label}: layers omitted must give ${OMITTED_COUNTS.join(', ')} and lists ${OMITTED_LISTS.join(', ')} of { name, reason }`);
  }
  return all;
}

/** A timeline's frame at time t: linear between frames, active stepped; wraps from its end to
 *  loopFrom when it loops, else holds its last frame. */
export function frameAt(value, t) {
  const frames = value.frames;
  let time = Math.max(t, 0);
  if (value.loop && value.length > value.loopFrom && time > value.length) {
    time = value.loopFrom + ((time - value.loopFrom) % (value.length - value.loopFrom));
  }
  if (time <= frames[0][0] || frames.length === 1) return frames[0].slice(1);
  const last = frames[frames.length - 1];
  if (time >= last[0]) return last.slice(1);
  let i = 0;
  while (frames[i + 1][0] <= time) i++;
  const a = frames[i], b = frames[i + 1];
  const k = (time - a[0]) / (b[0] - a[0]);
  return a.slice(1).map((v, j) => (j === 10 ? v : v + (b[j + 1] - v) * k));
}

/** The 2x3 matrix [a, b, c, d, tx, ty] a bone follower puts its layer under, from the bone now. */
export function followMatrix(follow, bone) {
  let rotation = follow.angle;
  let sx = 1, sy = 1;
  if (follow.rotation) {
    rotation = bone.getWorldRotationX();
    if (follow.mirrored) rotation = -rotation;
    if (follow.localScale && bone.scaleX < 0) rotation += 180;
  }
  if (follow.localScale) { sx = bone.scaleX; sy = bone.scaleY; }
  const r = (rotation * Math.PI) / 180, cos = Math.cos(r), sin = Math.sin(r);
  const [pa, pb, pc, pd] = follow.parent;
  // parent x R(rotation) x diag(sx, sy)
  const ra = cos * sx, rb = -sin * sy, rc = sin * sx, rd = cos * sy;
  return [pa * ra + pb * rc, pa * rb + pb * rd, pc * ra + pd * rc, pc * rb + pd * rd,
    follow.xy ? bone.worldX : follow.position[0], follow.xy ? bone.worldY : follow.position[1]];
}

/** Points (x, y pairs in the layer's own units; x, y, z triples when `solid`, a tilted entry's) in
 *  skeleton units for a frame (null for a static layer) and the bone it follows. */
export function layerVertices(layer, frame, bone, points = layer.vertices, solid = false) {
  const v = points;
  let m = frame ? frame.slice(0, 6) : [1, 0, 0, 1, 0, 0];
  let z = solid && frame ? [frame[15], frame[16]] : [0, 0];
  if (layer.follow) {
    const f = followMatrix(layer.follow, bone);
    m = [f[0] * m[0] + f[1] * m[2], f[0] * m[1] + f[1] * m[3], f[2] * m[0] + f[3] * m[2], f[2] * m[1] + f[3] * m[3],
      f[0] * m[4] + f[1] * m[5] + f[4], f[2] * m[4] + f[3] * m[5] + f[5]];
    z = [f[0] * z[0] + f[1] * z[1], f[2] * z[0] + f[3] * z[1]];
  }
  const stride = solid ? 3 : 2;
  const out = new Array((v.length / stride) * 2);
  for (let i = 0, o = 0; i < v.length; i += stride, o += 2) {
    const depth = solid ? v[i + 2] : 0;
    out[o] = m[0] * v[i] + m[1] * v[i + 1] + z[0] * depth + m[4];
    out[o + 1] = m[2] * v[i] + m[3] * v[i + 1] + z[1] * depth + m[5];
  }
  return out;
}

/** A polygon ([[u, v], ...]) clipped to the rectangle [u0, v0, u1, v1] (Sutherland-Hodgman). */
function clipToRect(polygon, [u0, v0, u1, v1]) {
  let points = polygon;
  const edges = [[(p) => p[0] >= u0, (a, b) => (u0 - a[0]) / (b[0] - a[0])], [(p) => p[0] <= u1, (a, b) => (u1 - a[0]) / (b[0] - a[0])],
    [(p) => p[1] >= v0, (a, b) => (v0 - a[1]) / (b[1] - a[1])], [(p) => p[1] <= v1, (a, b) => (v1 - a[1]) / (b[1] - a[1])]];
  for (const [inside, at] of edges) {
    const next = [];
    for (let i = 0; i < points.length; i++) {
      const a = points[i], b = points[(i + 1) % points.length];
      if (inside(a)) next.push(a);
      if (inside(a) !== inside(b)) { const k = at(a, b); next.push([a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k]); }
    }
    points = next;
    if (!points.length) break;
  }
  return points;
}

/**
 * The parts of a layer's mesh that show: each triangle cut to the texture's opaque box in UV space
 * and mapped back to the layer's own units, so a quad with a wide transparent margin frames by what
 * it draws. A triangle whose UVs leave 0-1 (a tiled texture) counts whole. `uvMap` is the frame's
 * [su, ou, sv, ov].
 */
export function visiblePoints(layer, opaque, uvMap = [1, 0, 1, 0], stride = 2) {
  const { vertices: p, uvs, triangles } = layer;
  const out = [];
  const uvAt = (i) => [uvs[i * 2] * uvMap[0] + uvMap[1], uvs[i * 2 + 1] * uvMap[2] + uvMap[3]];
  for (let t = 0; t < triangles.length; t += 3) {
    const ids = [triangles[t], triangles[t + 1], triangles[t + 2]];
    const uv = ids.map(uvAt);
    const whole = () => { for (const i of ids) for (let k = 0; k < stride; k++) out.push(p[i * stride + k]); };
    if (uv.some(([u, v]) => u < -1e-6 || u > 1 + 1e-6 || v < -1e-6 || v > 1 + 1e-6)) { whole(); continue; }
    const [a, b, c] = uv;
    const det = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]);
    if (Math.abs(det) < 1e-12) { whole(); continue; }
    for (const q of clipToRect(uv, opaque)) {
      const w1 = ((q[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (q[1] - a[1])) / det;
      const w2 = ((b[0] - a[0]) * (q[1] - a[1]) - (q[0] - a[0]) * (b[1] - a[1])) / det;
      const w0 = 1 - w1 - w2;
      for (let k = 0; k < stride; k++) out.push(w0 * p[ids[0] * stride + k] + w1 * p[ids[1] * stride + k] + w2 * p[ids[2] * stride + k]);
    }
  }
  return out;
}

/**
 * The frame the site opens on: the skeleton's bounds (Idle at its first frame) joined with every
 * layer drawn then (not an Interact/Special/Start effect, no delay, switched on and not fully
 * transparent at t = 0) where its texture shows, bone followers placed from the posed skeleton. Also checks every separator
 * slot and every follower's bone exist in the skeleton.
 */
export function layerBounds(doc, skeleton, skeletonBounds, label = 'layers', { effects = false } = {}) {
  const slots = new Set(skeleton.data.slots.map((slot) => slot.name));
  for (const name of doc.separators) if (!slots.has(name)) throw new Error(`${label}: separator slot ${name} is not in the skeleton`);
  const textures = [...doc.textures, ...(doc.effectTextures ?? [])];
  let x0 = skeletonBounds.x, y0 = skeletonBounds.y, x1 = skeletonBounds.x + skeletonBounds.width, y1 = skeletonBounds.y + skeletonBounds.height;
  for (const entry of doc.draw) {
    if (Object.hasOwn(entry, 'part')) continue;
    const solid = Object.hasOwn(entry, 'tilted');
    const effect = Object.hasOwn(entry, 'effect') || solid;
    const layer = solid ? entry.tilted : effect ? entry.effect : entry.layer;
    const bone = layer.follow ? skeleton.findBone(layer.follow.bone) : null;
    if (layer.follow && !bone) throw new Error(`${label}: ${layer.name} follows bone ${layer.follow.bone}, which the skeleton does not have`);
    // `bounds` frames what every reader draws; `effectBounds` the effects too (their main texture where
    // it shows, undistorted).
    if (effect && !effects) continue;
    if ((layer.only && layer.only !== 'Idle') || layer.delay > 0) continue;
    const frame = layer.animation ? frameAt(layer.animation, 0) : null;
    if (frame && frame[10] < 0.5) continue;
    if ((frame ? frame[9] : layer.color[3]) <= 0.001) continue;
    // An effect frames by where it shows at its first frame: its `visible`, in its own UVs (the exporter
    // measured it with its dissolves and colour); null: too little or nothing yet.
    if (effect && !layer.visible) continue;
    const points = layerVertices(layer, frame, bone, effect ? visiblePoints(layer, layer.visible, undefined, solid ? 3 : 2)
      : visiblePoints(layer, textures[layer.texture].opaque, frame ? frame.slice(11, 15) : undefined), solid);
    for (let i = 0; i < points.length; i += 2) {
      x0 = Math.min(x0, points[i]); x1 = Math.max(x1, points[i]);
      y0 = Math.min(y0, points[i + 1]); y1 = Math.max(y1, points[i + 1]);
    }
  }
  const x = Math.floor(x0), y = Math.floor(y0);
  return { x, y, width: Math.ceil(x1) - x, height: Math.ceil(y1) - y };
}
