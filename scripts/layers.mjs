// layers.json: the illustration prefab's own mesh layers (scripts/layers.py), checked and framed.
// Used by scripts/spine.mjs (the sync's bounds, re-read by the validator) and scripts/manifest.mjs.

const BLENDS = new Set(['alpha', 'add']);
const ONLY = new Set(['Idle', 'Interact', 'Special', 'Start']);
const WRAPS = new Set(['repeat', 'clamp', 'mirror']);
const OMITTED_COUNTS = ['particles', 'trails', 'skinned', 'hidden', 'holders'];
const OMITTED_LISTS = ['custom', 'externalTexture', 'other'];
const FRAME_LENGTH = 16; // [t, a, b, c, d, tx, ty, r, g, b, alpha, active, su, ou, sv, ov]
// Vertices further than this from the skeleton's origin (skeleton units) mean a unit went wrong.
const REACH = 100000;

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const finite = (value) => typeof value === 'number' && Number.isFinite(value);
const index = (value, length) => Number.isSafeInteger(value) && value >= 0 && value < length;

function timeline(value, label) {
  if (!isObject(value) || !finite(value.length) || value.length < 0 || typeof value.loop !== 'boolean' || !finite(value.loopFrom)
      || value.loopFrom < 0 || (value.loop && value.length > 0 && value.loopFrom >= value.length) || !Array.isArray(value.frames) || !value.frames.length) {
    throw new Error(`${label}: a timeline must be { length >= 0, loop, loopFrom, frames }`);
  }
  let last = -Infinity;
  value.frames.forEach((frame, i) => {
    if (!Array.isArray(frame) || frame.length !== FRAME_LENGTH || !frame.every(finite)) throw new Error(`${label}: frames[${i}] must be ${FRAME_LENGTH} numbers`);
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
export function layersShape(doc, label) {
  if (!isObject(doc) || doc.schemaVersion !== 1) throw new Error(`${label}: layers.json schemaVersion must be 1`);
  if (!Array.isArray(doc.textures)) throw new Error(`${label}: layers.json textures missing`);
  doc.textures.forEach((texture, i) => {
    if (!isObject(texture) || texture.file !== `layer${i}.webp`) throw new Error(`${label}: layers textures[${i}] must be layer${i}.webp`);
    if (!Number.isSafeInteger(texture.width) || texture.width <= 0 || !Number.isSafeInteger(texture.height) || texture.height <= 0) throw new Error(`${label}: layers textures[${i}] width/height missing`);
    if (!Number.isSafeInteger(texture.bytes) || texture.bytes <= 0 || typeof texture.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(texture.sha256)) throw new Error(`${label}: layers textures[${i}] bytes/sha256 missing`);
    if (!Array.isArray(texture.wrap) || texture.wrap.length !== 2 || !texture.wrap.every((w) => WRAPS.has(w))) throw new Error(`${label}: layers textures[${i}] wrap must be two of ${[...WRAPS].join(', ')}`);
  });
  const bounds = doc.bounds;
  if (!isObject(bounds) || !['x', 'y', 'width', 'height'].every((key) => finite(bounds[key])) || bounds.width <= 0 || bounds.height <= 0) throw new Error(`${label}: layers bounds missing`);
  if (!Array.isArray(doc.draw) || !doc.draw.length) throw new Error(`${label}: layers draw missing`);
  if (!Array.isArray(doc.separators) || !doc.separators.every((name) => typeof name === 'string' && name) || new Set(doc.separators).size !== doc.separators.length) {
    throw new Error(`${label}: layers separators must be distinct slot names`);
  }
  const used = new Set();
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
    if (!isObject(entry) || !isObject(entry.layer) || Object.keys(entry).length !== 1) throw new Error(`${at}: must be { part } or { layer }`);
    const layer = entry.layer;
    if (typeof layer.name !== 'string') throw new Error(`${at}: name missing`);
    if (!BLENDS.has(layer.blend)) throw new Error(`${at}: blend must be alpha or add`);
    if (!index(layer.texture, doc.textures.length)) throw new Error(`${at}: texture ${layer.texture} is not in textures`);
    used.add(layer.texture);
    const { vertices, uvs, colors, triangles } = layer;
    if (!Array.isArray(vertices) || vertices.length < 6 || vertices.length % 2 || !vertices.every(finite)) throw new Error(`${at}: vertices must be x, y pairs`);
    const count = vertices.length / 2;
    if (count > 65535) throw new Error(`${at}: ${count} vertices (16-bit indices)`);
    if (!Array.isArray(uvs) || uvs.length !== vertices.length || !uvs.every(finite)) throw new Error(`${at}: uvs must pair with vertices`);
    if (colors !== null && (!Array.isArray(colors) || colors.length !== count * 4 || !colors.every((c) => finite(c) && c >= 0 && c <= 1))) throw new Error(`${at}: colors must be null or r, g, b, a per vertex in 0-1`);
    if (!Array.isArray(triangles) || !triangles.length || triangles.length % 3 || !triangles.every((t) => index(t, count))) throw new Error(`${at}: triangles must index the vertices`);
    if (layer.animation === null) {
      if (!Array.isArray(layer.color) || layer.color.length !== 4 || !layer.color.every((c) => finite(c) && c >= 0)) throw new Error(`${at}: color must be [r, g, b, a]`);
    } else {
      if (layer.color !== null) throw new Error(`${at}: an animated layer takes its colour from its frames`);
      timeline(layer.animation, `${at} animation`);
      if (layer.animation.states !== undefined) {
        if (!isObject(layer.animation.states) || !Object.keys(layer.animation.states).length) throw new Error(`${at}: animation.states must name animations`);
        for (const [name, state] of Object.entries(layer.animation.states)) {
          if (!ONLY.has(name) || name === 'Idle') throw new Error(`${at}: animation.states.${name} is not a triggered animation`);
          timeline(state, `${at} animation.states.${name}`);
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
    if (layer.scroll !== null && (!Array.isArray(layer.scroll) || layer.scroll.length !== 2 || !layer.scroll.every(finite))) throw new Error(`${at}: scroll must be null or [u, v] per second`);
    if (layer.only !== null && !ONLY.has(layer.only)) throw new Error(`${at}: only must be null or ${[...ONLY].join(', ')}`);
    if (!finite(layer.delay) || layer.delay < 0) throw new Error(`${at}: delay must be seconds >= 0`);
    if (layer.follow === null && layer.animation === null && !vertices.every((v) => Math.abs(v) < REACH)) throw new Error(`${at}: vertices out of reach (${REACH} skeleton units)`);
  });
  if (!parts.size) throw new Error(`${label}: layers draw has no skeleton part`);
  doc.textures.forEach((texture, i) => { if (!used.has(i)) throw new Error(`${label}: layers ${texture.file} is not drawn by any layer`); });
  const omitted = doc.omitted;
  if (!isObject(omitted) || !OMITTED_COUNTS.every((k) => Number.isSafeInteger(omitted[k]) && omitted[k] >= 0)
      || !OMITTED_LISTS.every((k) => Array.isArray(omitted[k]) && omitted[k].every((item) => isObject(item) && typeof item.name === 'string' && typeof item.reason === 'string' && item.reason))) {
    throw new Error(`${label}: layers omitted must give ${OMITTED_COUNTS.join(', ')} and lists ${OMITTED_LISTS.join(', ')} of { name, reason }`);
  }
  return doc.textures;
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

/** A layer's vertices in skeleton units for a frame (null for a static layer) and the bone it follows. */
export function layerVertices(layer, frame, bone) {
  const v = layer.vertices;
  let m = frame ? frame.slice(0, 6) : [1, 0, 0, 1, 0, 0];
  if (layer.follow) {
    const f = followMatrix(layer.follow, bone);
    m = [f[0] * m[0] + f[1] * m[2], f[0] * m[1] + f[1] * m[3], f[2] * m[0] + f[3] * m[2], f[2] * m[1] + f[3] * m[3],
      f[0] * m[4] + f[1] * m[5] + f[4], f[2] * m[4] + f[3] * m[5] + f[5]];
  }
  const out = new Array(v.length);
  for (let i = 0; i < v.length; i += 2) {
    out[i] = m[0] * v[i] + m[1] * v[i + 1] + m[4];
    out[i + 1] = m[2] * v[i] + m[3] * v[i + 1] + m[5];
  }
  return out;
}

/**
 * The frame the site opens on: the skeleton's bounds (Idle at its first frame) joined with every
 * layer drawn then (not an Interact/Special/Start effect, no delay, switched on and not fully
 * transparent at t = 0), bone followers placed from the posed skeleton. Also checks every separator
 * slot and every follower's bone exist in the skeleton.
 */
export function layerBounds(doc, skeleton, skeletonBounds, label = 'layers') {
  const slots = new Set(skeleton.data.slots.map((slot) => slot.name));
  for (const name of doc.separators) if (!slots.has(name)) throw new Error(`${label}: separator slot ${name} is not in the skeleton`);
  let x0 = skeletonBounds.x, y0 = skeletonBounds.y, x1 = skeletonBounds.x + skeletonBounds.width, y1 = skeletonBounds.y + skeletonBounds.height;
  for (const entry of doc.draw) {
    if (Object.hasOwn(entry, 'part')) continue;
    const layer = entry.layer;
    const bone = layer.follow ? skeleton.findBone(layer.follow.bone) : null;
    if (layer.follow && !bone) throw new Error(`${label}: ${layer.name} follows bone ${layer.follow.bone}, which the skeleton does not have`);
    if ((layer.only && layer.only !== 'Idle') || layer.delay > 0) continue;
    const frame = layer.animation ? frameAt(layer.animation, 0) : null;
    if (frame && frame[10] < 0.5) continue;
    if ((frame ? frame[9] : layer.color[3]) <= 0.001) continue;
    const points = layerVertices(layer, frame, bone);
    for (let i = 0; i < points.length; i += 2) {
      x0 = Math.min(x0, points[i]); x1 = Math.max(x1, points[i]);
      y0 = Math.min(y0, points[i + 1]); y1 = Math.max(y1, points[i + 1]);
    }
  }
  const x = Math.floor(x0), y = Math.floor(y0);
  return { x, y, width: Math.ceil(x1) - x, height: Math.ceil(y1) - y };
}
