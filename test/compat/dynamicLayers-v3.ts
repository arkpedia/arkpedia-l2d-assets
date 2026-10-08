/*
 * The illustration prefab's own mesh layers: the skies, windows, frames and glows the game draws with
 * an animated outfit's skeleton (layers.json in arkpedia/arkpedia-l2d-assets, written by its
 * scripts/layers.py). This module reads and checks that file and works out, frame by frame, what
 * each layer shows: where the skeleton's parts start and end in its draw order, where a layer sits
 * (on its own, on its timeline, or on the bone it follows) and whether it is visible. Drawing them
 * is components/skins/dynamicLayerRenderer.ts's job; nothing here touches WebGL, so the logic is
 * unit tested (scripts/tests/test-dynamic-art.ts). Only DynamicArtwork, which loads when the reader
 * presses Animated, imports it.
 *
 * A layer drawn with one of the game's effect shaders (layers.json `effect` entries, and a plain
 * layer's `exact`: flow distortion, dissolve, ramp, vertex disturbance, Disturb2's noise) carries
 * the shader's parameters as data (LayerShader, the assets repository's scripts/effects.py). Its UVs
 * are then the mesh's own, in Unity's space (v up): the renderer applies each texture's tiling and
 * flips v only when it samples. A scroll speed an Animator drives comes as an `offset` parameter:
 * the speed's integral over the layer's own time, carried on past its timeline (continueOffsets).
 *
 * A `tilted` entry (layersVersion 3) is an effect layer whose mesh turns in depth while it moves (a
 * tumbling star, a sword swung through the screen): its vertices are 3D and each frame carries the
 * depth column of the 2x4 orthographic projection of its transform (the game's illustration camera is
 * orthographic), so the renderer projects it frame by frame and culls the faces that turn away.
 */

/** A time-ordered timeline: frames [t, a, b, c, d, tx, ty, r, g, b, alpha, active, su, ou, sv, ov], a
 *  tilted layer's with [e, f] (its projection's depth column) after them, then any shader parameters. */
export interface LayerTimeline {
  length: number;
  loop: boolean;
  /** After `length`, a looping timeline wraps back to here. */
  loopFrom: number;
  frames: number[][];
}

export interface LayerFollow {
  bone: string;
  xy: boolean;
  rotation: boolean;
  localScale: boolean;
  /** A mirrored parent negates the bone's rotation (spine-unity's BoneFollower). */
  mirrored: boolean;
  /** Skeleton units per follower unit, [a, b, c, d]. */
  parent: [number, number, number, number];
  /** Used when `xy` is off. */
  position: [number, number];
  /** Degrees, used when `rotation` is off. */
  angle: number;
}

/** The animations whose controller groups switch layers, and the ones that trigger Animator states. */
export type LayerAnimation = 'Idle' | 'Interact' | 'Special' | 'Start';

/** A texture an effect samples besides the main one: its index (null: an unbound noise map, which
 *  reads 0), its tiling [sx, sy, ox, oy], the shader's own scroll (speed: wrapped, fract(t x speed))
 *  and a UV scroll script's (scroll: not wrapped), Unity UV units per second. */
export interface ShaderMap { texture: number | null; st: Vec4; speed: Vec2; scroll: Vec2; offset?: Vec2 }
export interface DistortMap extends ShaderMap { anchor: Vec2; intensity: Vec2 }
export interface DissolveMap extends ShaderMap { fract: boolean; amount: number; border: number }
/** `offset` (set from a frame by animatedShader): the integrated speed an Animator drives, in place of t x speed. */
export interface MainMap { st: Vec4; speed: Vec2; scroll: Vec2; fract: boolean; offset?: Vec2 }
type Vec2 = [number, number];
type Vec3 = [number, number, number];
type Vec4 = [number, number, number, number];

/** The Torappu particle shaders: texture x colour x vertex colour [x ramp], the main UV moved by a
 *  flow distortion (after the main tiling: space 'main'; on the mesh UV before it: 'raw'), alpha cut
 *  by up to two dissolves, an edge colour, vertices moved by a noise texture. */
export interface ParticleShader {
  family: 'particle';
  main: MainMap;
  distort: { space: 'main' | 'raw'; main: number; dissolve: number; constant: Vec2; maps: DistortMap[]; weight: ShaderMap | null } | null;
  dissolve: DissolveMap[];
  edge: { color: Vec4; pow: number; epsilon: boolean } | null;
  ramp: ShaderMap | null;
  /** Mesh units moved by (texture.rgb - 0.5) x weight x intensity, mapped by matrix [a, b, c, d, e, f] into the vertices' units. */
  vertex: (ShaderMap & { intensity: Vec3; weight: ShaderMap | null; matrix: [number, number, number, number, number, number] }) | null;
  /** Parameters an Animator drives (dissolve.0.amount, distort.maps.0.st, main.offset...): their values
   *  follow the layer's frames, past the 16 (18) numbers every frame has, in this order (animatedShader). */
  animated: string[];
}

/** Disturb2: two noise channels move the main UV; its colour has no x2 (layers.json gives the material colour).
 *  Animated: noise1, noise2, glow and noise.offset (the two channels' integrated scroll, in `noise.offset`). */
export interface NoiseShader { family: 'noise'; mode: 'default' | 'add' | 'glow'; main: MainMap; noise: ShaderMap; noise1: Vec4; noise2: Vec4; glow: Vec4 | null; animated: string[] }

export type LayerShader = ParticleShader | NoiseShader;

export interface DynamicLayer {
  name: string;
  blend: 'alpha' | 'add';
  /** Its main texture; null only for a shader layer whose material binds none (Unity's white). */
  texture: number | null;
  /** [r, g, b, a] for a layer without a timeline; null when its frames carry the colour. */
  color: [number, number, number, number] | null;
  vertices: Float32Array;
  uvs: Float32Array;
  colors: Float32Array | null;
  triangles: Uint16Array;
  follow: LayerFollow | null;
  animation: (LayerTimeline & { states: Partial<Record<LayerAnimation, LayerTimeline>> | null }) | null;
  /** Image-space UV units per second. */
  scroll: [number, number] | null;
  only: LayerAnimation | null;
  delay: number;
  /** The effect shader it is drawn with; null for a plain layer. With one, `uvs` are the mesh's own
   *  (Unity space) and `scroll` is null (the shader's `main` scrolls). */
  shader: LayerShader | null;
  /** Faces culled as it is drawn (an effect entry that moves): 0 none, 1 front, 2 back. Unity's front
   *  faces wind clockwise on screen (anticlockwise under a mirroring transform: the file has folded
   *  in what it knows, the renderer what a bone follower adds). */
  cull: 0 | 1 | 2;
  /** A tilted entry: `vertices` are x, y, z triples and the frames carry the depth column [e, f]. */
  solid: boolean;
  /** Where each animated offset parameter starts in the pose's `extra` (its [u, v, speed u, speed v]). */
  offsets: number[];
}

export interface LayerTexture { file: string; width: number; height: number; wrap: ['repeat' | 'clamp' | 'mirror', 'repeat' | 'clamp' | 'mirror'] }

export type LayerEntry = { kind: 'part'; index: number } | { kind: 'layer'; layer: DynamicLayer };

export interface DynamicLayers {
  textures: LayerTexture[];
  bounds: { x: number; y: number; width: number; height: number };
  separators: string[];
  /** Back to front. */
  draw: LayerEntry[];
  /** Layers dropped because this reader did not understand them (a newer file); the rest still draw. */
  skipped: number;
}

/* --- effect shaders ------------------------------------------------------------------------ */

const vec = (value: unknown, length: number): value is number[] => Array.isArray(value) && value.length === length && value.every(finite);
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]) => {
  const own = Object.keys(value);
  return own.length === keys.length && keys.every((key) => Object.hasOwn(value, key));
};
const MAP_KEYS = ['texture', 'st', 'speed', 'scroll'] as const;
/** How many numbers an animated parameter adds to each frame (an offset: [u, v, speed u, speed v]). */
function parameterWidth(path: string): number {
  if (path === 'vertex.intensity') return 3;
  return ({ amount: 1, border: 1, st: 4, intensity: 2, color: 4, offset: 4, main: 1, dissolve: 1, noise1: 4, noise2: 4, glow: 4 } as Record<string, number>)[path.split('.').at(-1)!] ?? 0;
}

/** The numbers a particle shader's animated parameters add to each frame, or -1 when one names a
 *  member the shader does not have. */
export function animatedWidth(shader: ParticleShader): number {
  let width = 0;
  const d = shader.distort;
  for (const path of shader.animated) {
    const p = path.split('.');
    const at = Number(p[p.length === 3 ? 1 : 2]);
    const ok = (p[0] === 'dissolve' && p.length === 3 && Number.isInteger(at) && at < shader.dissolve.length && ['amount', 'border', 'st', 'offset'].includes(p[2]))
      || (p[0] === 'distort' && !!d && p[1] === 'maps' && p.length === 4 && Number.isInteger(at) && at < d.maps.length
        && (p[3] === 'intensity' || ((p[3] === 'st' || p[3] === 'offset') && d.maps[at].texture !== null)))
      || ((path === 'distort.main' || path === 'distort.dissolve') && !!d) || path === 'main.offset'
      || (path === 'distort.weight.st' && !!d?.weight) || (path === 'ramp.st' && !!shader.ramp)
      || ((path === 'vertex.st' || path === 'vertex.intensity' || path === 'vertex.offset') && !!shader.vertex)
      || (path === 'vertex.weight.st' && !!shader.vertex?.weight) || (path === 'edge.color' && !!shader.edge);
    if (!ok) return -1;
    width += parameterWidth(path);
  }
  return width;
}

/** The numbers a Disturb2 shader's animated parameters add to each frame, or -1 for one it does not have. */
export function noiseAnimatedWidth(shader: NoiseShader): number {
  let width = 0;
  for (const path of shader.animated) {
    if (!['noise1', 'noise2', 'noise.offset'].includes(path) && !(path === 'glow' && shader.glow !== null)) return -1;
    width += parameterWidth(path);
  }
  return width;
}

/** Where each animated offset parameter starts among a frame's parameters. */
export function offsetColumns(shader: LayerShader): number[] {
  const out: number[] = [];
  let at = 0;
  for (const path of shader.animated) {
    if (path.endsWith('.offset')) out.push(at);
    at += parameterWidth(path);
  }
  return out;
}

/** The shader with its animated parameters at a frame's values (`extra`: the frame past its 16 (18)
 *  numbers). An offset sets its map's `offset` (its first two numbers; the speed after them only carries
 *  the offset on, continueOffsets). */
export function animatedShader(shader: LayerShader, extra: readonly number[]): LayerShader {
  if (!shader.animated.length) return shader;
  const copy: LayerShader = structuredClone(shader);
  let at = 0;
  for (const path of shader.animated) {
    const n = parameterWidth(path);
    const values = extra.slice(at, at + n);
    at += n;
    if (values.length < n) break;
    const parts = path.split('.');
    let node: Record<string, unknown> = copy as unknown as Record<string, unknown>;
    for (const part of parts.slice(0, -1)) node = (Array.isArray(node) ? node[Number(part)] : node[part]) as Record<string, unknown>;
    const key = parts[parts.length - 1];
    if (key === 'offset') node.offset = [values[0], values[1]];
    else node[key] = n === 1 ? values[0] : values;
  }
  return copy;
}

/** At most this many noise maps and dissolves on one layer (the shaders have two of each). */
const MAX_DISTORT = 2;
const MAX_DISSOLVE = 2;
/** Speeds, intensities and tilings far beyond any the game uses mean the file is wrong. */
const REASONABLE = 1e4;

function shaderMap(value: unknown, textures: number, extra: readonly string[] = [], nullable = false): ShaderMap | null {
  if (!isObject(value) || !exactKeys(value, [...MAP_KEYS, ...extra])) return null;
  const texture = value.texture;
  if (!(texture === null && nullable) && !(Number.isSafeInteger(texture) && (texture as number) >= 0 && (texture as number) < textures)) return null;
  if (!vec(value.st, 4) || !vec(value.speed, 2) || !vec(value.scroll, 2)) return null;
  if (![...value.st as number[], ...value.speed as number[], ...value.scroll as number[]].every((v) => Math.abs(v) < REASONABLE)) return null;
  return { texture: texture as number | null, st: value.st.slice() as Vec4, speed: value.speed.slice() as Vec2, scroll: value.scroll.slice() as Vec2 };
}

function mainMap(value: unknown): MainMap | null {
  if (!isObject(value) || !exactKeys(value, ['st', 'speed', 'scroll', 'fract']) || !vec(value.st, 4) || !vec(value.speed, 2) || !vec(value.scroll, 2)
    || typeof value.fract !== 'boolean') return null;
  return { st: value.st.slice() as Vec4, speed: value.speed.slice() as Vec2, scroll: value.scroll.slice() as Vec2, fract: value.fract };
}

/** A layer's `shader`, checked strictly: a family this reader draws, every member it knows and no
 *  other, every texture in range. Null when anything is not understood (the layer is then left out,
 *  or drawn plain where the file offers that). */
export function parseShader(value: unknown, textures: number): LayerShader | null {
  if (!isObject(value)) return null;
  const main = mainMap(value.main);
  if (!main) return null;
  if (value.family === 'noise') {
    if (!exactKeys(value, ['family', 'mode', 'main', 'noise', 'noise1', 'noise2', 'glow', 'animated'])) return null;
    const animated = value.animated;
    if (!Array.isArray(animated) || !animated.every((p) => typeof p === 'string') || new Set(animated).size !== animated.length) return null;
    if (value.mode !== 'default' && value.mode !== 'add' && value.mode !== 'glow') return null;
    const noise = shaderMap(value.noise, textures);
    if (!noise || !vec(value.noise1, 4) || !vec(value.noise2, 4)) return null;
    if (value.glow !== null && !vec(value.glow, 4)) return null;
    if ((value.mode === 'glow') !== (value.glow !== null)) return null;
    const shader: NoiseShader = { family: 'noise', mode: value.mode, main, noise, noise1: value.noise1.slice() as Vec4, noise2: value.noise2.slice() as Vec4,
      glow: value.glow ? (value.glow as number[]).slice() as Vec4 : null, animated: (animated as string[]).slice() };
    return noiseAnimatedWidth(shader) < 0 ? null : shader;
  }
  if (value.family !== 'particle' || !exactKeys(value, ['family', 'main', 'distort', 'dissolve', 'edge', 'ramp', 'vertex', 'animated'])) return null;
  const animated = value.animated;
  if (!Array.isArray(animated) || !animated.every((p) => typeof p === 'string') || new Set(animated).size !== animated.length) return null;
  let distort: ParticleShader['distort'] = null;
  if (value.distort !== null) {
    const d = value.distort;
    if (!isObject(d) || !exactKeys(d, ['space', 'main', 'dissolve', 'constant', 'maps', 'weight']) || (d.space !== 'main' && d.space !== 'raw')
      || !finite(d.main) || !finite(d.dissolve) || !vec(d.constant, 2) || !Array.isArray(d.maps) || d.maps.length > MAX_DISTORT) return null;
    const maps: DistortMap[] = [];
    for (const raw of d.maps) {
      const map = shaderMap(raw, textures, ['anchor', 'intensity'], true);
      if (!map || !vec((raw as Record<string, unknown>).anchor, 2) || !vec((raw as Record<string, unknown>).intensity, 2)) return null;
      maps.push({ ...map, anchor: ((raw as Record<string, number[]>).anchor).slice() as Vec2, intensity: ((raw as Record<string, number[]>).intensity).slice() as Vec2 });
    }
    const weight = d.weight === null ? null : shaderMap(d.weight, textures);
    if (d.weight !== null && !weight) return null;
    distort = { space: d.space, main: d.main, dissolve: d.dissolve, constant: (d.constant as number[]).slice() as Vec2, maps, weight };
  }
  if (!Array.isArray(value.dissolve) || value.dissolve.length > MAX_DISSOLVE) return null;
  const dissolve: DissolveMap[] = [];
  for (const raw of value.dissolve) {
    const map = shaderMap(raw, textures, ['fract', 'amount', 'border']);
    const r = raw as Record<string, unknown>;
    if (!map || typeof r.fract !== 'boolean' || !finite(r.amount) || !finite(r.border) || !(r.border > 0)) return null;
    dissolve.push({ ...map, fract: r.fract, amount: r.amount, border: r.border });
  }
  let edge: ParticleShader['edge'] = null;
  if (value.edge !== null) {
    const e = value.edge;
    if (!isObject(e) || !exactKeys(e, ['color', 'pow', 'epsilon']) || !vec(e.color, 4) || !finite(e.pow) || typeof e.epsilon !== 'boolean') return null;
    edge = { color: (e.color as number[]).slice() as Vec4, pow: e.pow, epsilon: e.epsilon };
  }
  const ramp = value.ramp === null ? null : shaderMap(value.ramp, textures);
  if (value.ramp !== null && !ramp) return null;
  let vertex: ParticleShader['vertex'] = null;
  if (value.vertex !== null) {
    const v = value.vertex as Record<string, unknown>;
    const map = shaderMap(v, textures, ['intensity', 'weight', 'matrix']);
    if (!map || !vec(v.intensity, 3) || !vec(v.matrix, 6)) return null;
    const weight = v.weight === null ? null : shaderMap(v.weight, textures);
    if (v.weight !== null && !weight) return null;
    vertex = { ...map, intensity: (v.intensity as number[]).slice() as Vec3, weight, matrix: (v.matrix as number[]).slice() as [number, number, number, number, number, number] };
  }
  const shader: ParticleShader = { family: 'particle', main, distort, dissolve, edge, ramp, vertex, animated: (animated as string[]).slice() };
  return animatedWidth(shader) < 0 ? null : shader;
}

/** GLSL's roundEven: halves to the even neighbour (JavaScript's Math.round sends them up). */
export function roundEven(x: number): number {
  const floor = Math.floor(x);
  const rest = x - floor;
  if (rest !== 0.5) return Math.round(x) + 0;
  return (floor % 2 === 0 ? floor : floor + 1) + 0;
}

/** A dissolve's k: alpha x clamp((texture - amount + border x k) / border), k = 1 - roundEven(amount + 0.5). */
export function dissolveK(amount: number): number {
  return 1 - roundEven(amount + 0.5);
}

/** A map's UV offset at time t (Unity space): the shader's own scroll wrapped as fract(t x speed) (or, for a
 *  speed an Animator drives, its integrated `offset`, wrapped the same way), plus a scroll script's, which
 *  only wraps where the texture repeats (it grows without end in the game). */
export function mapOffset(map: Pick<ShaderMap, 'speed' | 'scroll' | 'offset'>, t: number, wrap: readonly ['repeat' | 'clamp' | 'mirror', 'repeat' | 'clamp' | 'mirror'] = ['repeat', 'repeat']): Vec2 {
  const scrolled = (x: number, mode: string) => (mode === 'repeat' ? wrapUnit(x) : mode === 'mirror' ? x - Math.floor(x / 2) * 2 : x);
  const own = map.offset ?? [map.speed[0] * t, map.speed[1] * t];
  return [wrapUnit(own[0]) + scrolled(map.scroll[0] * t, wrap[0]), wrapUnit(own[1]) + scrolled(map.scroll[1] * t, wrap[1])];
}

/** A frame's UV map ([su, ou, sv, ov] on image-space coordinates, v down) as the same map on Unity-space
 *  coordinates (v up): u' = u su + ou, v' = v sv + (1 - sv - ov). */
export function unityUvMap(uv: readonly number[]): Vec4 {
  return [uv[0], uv[1], uv[2], 1 - uv[2] - uv[3]];
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const isObject = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const ANIMATIONS = new Set<LayerAnimation>(['Idle', 'Interact', 'Special', 'Start']);
const WRAPS = new Set(['repeat', 'clamp', 'mirror']);
const FRAME = 16;
/** A tilted layer's frames: the 16, then its projection's depth column [e, f]. */
const SOLID_FRAME = 18;

function timeline(value: unknown, width = FRAME): LayerTimeline | null {
  if (!isObject(value) || !finite(value.length) || value.length < 0 || typeof value.loop !== 'boolean' || !finite(value.loopFrom) || value.loopFrom < 0) return null;
  const frames = value.frames;
  if (!Array.isArray(frames) || !frames.length || frames.length > LIMITS.frames) return null;
  for (let i = 0; i < frames.length; i++) {
    const frame = frames[i];
    if (!Array.isArray(frame) || frame.length !== width || !frame.every(finite) || (i === 0 ? frame[0] !== 0 : frame[0] <= frames[i - 1][0])) return null;
  }
  return { length: value.length, loop: value.loop, loopFrom: value.loopFrom, frames: frames.map((frame: number[]) => frame.slice()) };
}

/** One layer, checked against the file's textures; null when anything about it is not understood. A
 *  shader layer (an `effect` entry, or a plain layer's `exact` laid over it) has no `scroll` of its own;
 *  a `solid` one (a tilted entry) has x, y, z vertices and a timeline whose frames carry its depth column. */
function parseLayer(value: unknown, textures: number, shaderValue?: unknown, solid = false): DynamicLayer | null {
  if (!isObject(value) || typeof value.name !== 'string' || (value.blend !== 'alpha' && value.blend !== 'add')) return null;
  let shader: LayerShader | null = null;
  if (shaderValue !== undefined) {
    shader = parseShader(shaderValue, textures);
    if (!shader) return null;
  }
  if (solid && !shader) return null;
  if (!(shader && value.texture === null) && !(Number.isSafeInteger(value.texture) && (value.texture as number) >= 0 && (value.texture as number) < textures)) return null;
  const { vertices, uvs, colors, triangles } = value;
  const stride = solid ? 3 : 2;
  if (!Array.isArray(vertices) || vertices.length < 3 * stride || vertices.length % stride || !vertices.every(finite)) return null;
  const count = vertices.length / stride;
  if (count > 65535 || !Array.isArray(uvs) || uvs.length !== count * 2 || !uvs.every(finite)) return null;
  if (colors !== null && !(Array.isArray(colors) && colors.length === count * 4 && colors.every(finite))) return null;
  if (!Array.isArray(triangles) || !triangles.length || triangles.length % 3 || !triangles.every((t) => Number.isSafeInteger(t) && t >= 0 && t < count)) return null;
  let animation: DynamicLayer['animation'] = null;
  let color: DynamicLayer['color'] = null;
  // An effect's animated parameters ride on its frames, past the 16 (a tilted layer's 18) numbers every frame has.
  const width = (solid ? SOLID_FRAME : FRAME) + (shader ? (shader.family === 'particle' ? animatedWidth(shader) : noiseAnimatedWidth(shader)) : 0);
  if (solid && value.animation === null) return null;
  if (value.animation === null) {
    if (!Array.isArray(value.color) || value.color.length !== 4 || !value.color.every(finite) || width !== FRAME) return null;
    color = [value.color[0], value.color[1], value.color[2], value.color[3]];
  } else {
    const main = timeline(value.animation, width);
    if (!main) return null;
    let states: Partial<Record<LayerAnimation, LayerTimeline>> | null = null;
    const raw = (value.animation as Record<string, unknown>).states;
    if (raw !== undefined) {
      if (!isObject(raw)) return null;
      states = {};
      for (const [name, state] of Object.entries(raw)) {
        const parsed = timeline(state, width);
        if (!ANIMATIONS.has(name as LayerAnimation) || !parsed) return null;
        states[name as LayerAnimation] = parsed;
      }
    }
    animation = { ...main, states };
  }
  let follow: LayerFollow | null = null;
  if (value.follow !== null) {
    const f = value.follow;
    if (!isObject(f) || typeof f.bone !== 'string' || !f.bone || !['xy', 'rotation', 'localScale', 'mirrored'].every((k) => typeof f[k] === 'boolean')
      || !Array.isArray(f.parent) || f.parent.length !== 4 || !f.parent.every(finite) || !Array.isArray(f.position) || f.position.length !== 2
      || !f.position.every(finite) || !finite(f.angle)) return null;
    follow = {
      bone: f.bone, xy: f.xy as boolean, rotation: f.rotation as boolean, localScale: f.localScale as boolean, mirrored: f.mirrored as boolean,
      parent: [f.parent[0], f.parent[1], f.parent[2], f.parent[3]], position: [f.position[0], f.position[1]], angle: f.angle,
    };
  }
  if (shader ? value.scroll != null : value.scroll !== null && !(Array.isArray(value.scroll) && value.scroll.length === 2 && value.scroll.every(finite))) return null;
  if (value.only !== null && !ANIMATIONS.has(value.only as LayerAnimation)) return null;
  if (!finite(value.delay) || value.delay < 0) return null;
  const cull = value.cull ?? 0;
  if (cull !== 0 && cull !== 1 && cull !== 2) return null;
  return {
    name: value.name, blend: value.blend, texture: value.texture as number | null, color,
    vertices: new Float32Array(vertices), uvs: new Float32Array(uvs), colors: colors ? new Float32Array(colors as number[]) : null,
    triangles: new Uint16Array(triangles), follow, animation,
    scroll: !shader && value.scroll ? [(value.scroll as number[])[0], (value.scroll as number[])[1]] : null,
    only: (value.only as LayerAnimation | null) ?? null, delay: value.delay, shader, cull, solid, offsets: shader ? offsetColumns(shader) : [],
  };
}

/** A plain layer drawn exactly with its `exact` (the mesh's own UVs and the shader), or null when this
 *  reader does not understand it (the plain approximation then draws). */
function parseExact(value: Record<string, unknown>, textures: number): DynamicLayer | null {
  const exact = value.exact;
  if (!isObject(exact) || !exactKeys(exact, ['uvs', 'shader'])) return null;
  return parseLayer({ ...value, uvs: exact.uvs, scroll: undefined, cull: 0 }, textures, exact.shader);
}

/** A file name inside the model's folder: never a path. */
const LAYER_FILE = /^layer\d+\.webp$/;

/** Bounds on what one layers.json may ask of the browser (each texture is a fetch and a GPU upload).
 *  The 88 Global models stay far inside them: at most 21 textures, 60 draw entries, a few hundred frames. */
export const LIMITS = { textures: 128, draw: 1024, frames: 20000 };

/**
 * A layers.json read from the network, checked before any texture is fetched on its word: textures
 * are layer<N>.webp files in the model's own folder, in order. The file as a whole must be one this
 * reader knows (else it throws, and the artwork plays without its layers); a single layer it does not
 * understand is dropped and counted, the others still draw.
 */
/** x wrapped into [0, 1), as GLSL fract(); -0 comes out 0. */
export function wrapUnit(x: number): number {
  return x - Math.floor(x) + 0;
}

export function parseDynamicLayers(value: unknown): DynamicLayers {
  const fail = (why: string): never => { throw new Error(`Unsupported layers.json: ${why}`); };
  if (!isObject(value) || value.schemaVersion !== 1) return fail(`schemaVersion ${String(isObject(value) ? value.schemaVersion : value)}`);
  // Plain layers' textures, then those only effects sample (numbered on: layer<N>.webp throughout).
  const effectTextures = value.effectTextures ?? [];
  if (!Array.isArray(value.textures) || !Array.isArray(effectTextures)) return fail('textures');
  const textures = [...value.textures, ...effectTextures];
  if (textures.length > LIMITS.textures) return fail('textures');
  const parsedTextures = textures.map((texture, i): LayerTexture => {
    if (!isObject(texture) || texture.file !== `layer${i}.webp` || !LAYER_FILE.test(String(texture.file)) || !finite(texture.width) || !finite(texture.height)
      || !Array.isArray(texture.wrap) || texture.wrap.length !== 2 || !texture.wrap.every((w) => WRAPS.has(w as string))) return fail(`textures[${i}]`);
    return { file: texture.file as string, width: texture.width as number, height: texture.height as number, wrap: [texture.wrap[0], texture.wrap[1]] as LayerTexture['wrap'] };
  });
  // The frame with the effects drawn too, from a file that has them; the plain one otherwise.
  const bounds = isObject(value.effectBounds) ? value.effectBounds : value.bounds;
  if (!isObject(bounds) || ![bounds.x, bounds.y, bounds.width, bounds.height].every(finite) || !((bounds.width as number) > 0 && (bounds.height as number) > 0)) return fail('bounds');
  const separators = value.separators;
  if (!Array.isArray(separators) || !separators.every((s) => typeof s === 'string' && s)) return fail('separators');
  if (!Array.isArray(value.draw) || value.draw.length > LIMITS.draw) return fail('draw');
  const draw: LayerEntry[] = [];
  let skipped = 0;
  const parts = new Set<number>();
  for (const entry of value.draw) {
    if (isObject(entry) && Object.hasOwn(entry, 'part')) {
      const index = entry.part;
      if (!Number.isSafeInteger(index) || (index as number) < 0 || (index as number) > separators.length || parts.has(index as number)) return fail('part');
      parts.add(index as number);
      draw.push({ kind: 'part', index: index as number });
      continue;
    }
    let layer: DynamicLayer | null = null;
    if (isObject(entry) && isObject(entry.effect)) layer = parseLayer(entry.effect, parsedTextures.length, entry.effect.shader);
    else if (isObject(entry) && isObject(entry.tilted)) layer = parseLayer(entry.tilted, parsedTextures.length, entry.tilted.shader, true);
    else if (isObject(entry) && isObject(entry.layer)) {
      // A plain layer culls nothing as it is drawn (layers.json puts a culled moving one in an effect entry).
      const plain = { ...entry.layer, cull: 0 };
      layer = entry.layer.exact != null ? parseExact(plain, parsedTextures.length) : null;
      layer ??= parseLayer(plain, parsedTextures.length);
    }
    if (layer) draw.push({ kind: 'layer', layer });
    else skipped++;
  }
  if (!parts.size) return fail('no skeleton part');
  return {
    textures: parsedTextures,
    bounds: { x: bounds.x as number, y: bounds.y as number, width: bounds.width as number, height: bounds.height as number },
    separators: separators.slice() as string[],
    draw,
    skipped,
  };
}

/* --- the skeleton's parts ---------------------------------------------------------------- */

export interface SlotRef { name: string; index: number; active: boolean }

/**
 * Each part's slot range for spine-ts' drawSkeleton(skeleton, pma, start, end): part k holds the slots
 * from the k-th separator met in the current draw order to the next (a separator starts its part),
 * as SkeletonRenderSeparator splits them. The range is the slot data indices of the part's first and
 * last active slot (spine-ts skips inactive bones before it looks at the range); null for a part
 * with nothing to draw.
 */
export function partRanges(drawOrder: readonly SlotRef[], separators: readonly string[], parts: number): ([number, number] | null)[] {
  const ranges: ([number, number] | null)[] = Array.from({ length: parts }, () => null);
  const split = new Set(separators);
  let part = 0;
  for (const slot of drawOrder) {
    if (split.has(slot.name)) part++;
    if (part >= parts || !slot.active) continue;
    const range = ranges[part];
    if (range) range[1] = slot.index;
    else ranges[part] = [slot.index, slot.index];
  }
  return ranges;
}

/* --- timelines ------------------------------------------------------------------------- */

/** A timeline's values at time t: linear between frames, `active` stepped; after its length it wraps
 *  to loopFrom when it loops, else holds its last frame. */
export function frameAt(value: LayerTimeline, t: number): number[] {
  const frames = value.frames;
  let time = Math.max(t, 0);
  if (value.loop && value.length > value.loopFrom && time > value.length) {
    time = value.loopFrom + ((time - value.loopFrom) % (value.length - value.loopFrom));
  }
  if (frames.length === 1 || time <= frames[0][0]) return frames[0].slice(1);
  const last = frames[frames.length - 1];
  if (time >= last[0]) return last.slice(1);
  let lo = 0, hi = frames.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (frames[mid][0] <= time) lo = mid; else hi = mid;
  }
  const a = frames[lo], b = frames[hi];
  const k = (time - a[0]) / (b[0] - a[0]);
  return a.slice(1).map((v, j) => (j === 10 ? v : v + (b[j + 1] - v) * k));
}

/** What the player knows each frame: the illustration's animation on track 0 and how long it has
 *  played, and how long the illustration has run since it took over (both 0 under reduced motion,
 *  where layers stand at their first frame). */
export interface LayerClock {
  /** The animation playing on track 0. */
  playing: string | null;
  animationTime: number;
  time: number;
}

/** What track 0 is doing, as the player reads it from spine's AnimationState. */
export interface TrackState { name: string; trackTime: number; animationEnd: number; timeScale: number }

/**
 * The layers' clock for one frame. With motion: the illustration's running time, and the current
 * animation's track time. Under reduced motion nothing moves on its own (time 0), but an animation the
 * reader pressed (a track that is not the still pose, timeScale 0) drives its own layers on its clock,
 * held at its end once it finishes: its flash or glow plays and goes, instead of standing at frame 0.
 */
export function layerClock(reduced: boolean, track: TrackState | null, illustrationTime: number): LayerClock {
  const playing = track?.name ?? null;
  if (!reduced) return { playing, animationTime: track?.trackTime ?? 0, time: illustrationTime };
  const pressed = !!track && track.timeScale !== 0;
  return { playing, animationTime: pressed ? Math.min(track!.trackTime, track!.animationEnd) : 0, time: 0 };
}

export interface LayerPose {
  /** Mesh units to skeleton units (or to the follower's units under a bone follower): [a, b, c, d, tx, ty]. */
  matrix: [number, number, number, number, number, number];
  /** A tilted layer's depth column [e, f]: x' = a x + b y + e z + tx, y' = c x + d y + f z + ty. */
  depth: [number, number];
  color: [number, number, number, number];
  /** On the exported UVs: u' = u su + ou, v' = v sv + ov (scroll included). */
  uv: [number, number, number, number];
  /** The layer's own time (after its delay), which its shader scrolls with. */
  t: number;
  /** Its shader's animated parameters now (the frame past its 16 (18) numbers; empty without), offsets
   *  carried on past the timeline (continueOffsets). */
  extra: number[];
}

/**
 * Integrated offsets past a timeline's end: a looping one adds what one loop moved (the offset at its end
 * less the offset where it loops from) once per loop, so the texture runs on instead of jumping back;
 * one that holds its last frame runs on at its last speed. `extra` is frameAt's parameters at the wrapped
 * time; `at` where the parameters start in a frame (after its first number); `offsets` where each offset's
 * [u, v, speed u, speed v] starts among them.
 */
export function continueOffsets(line: LayerTimeline, t: number, extra: number[], offsets: readonly number[], at: number): number[] {
  if (!offsets.length) return extra;
  const out = extra.slice();
  const last = line.frames[line.frames.length - 1];
  if (line.loop && line.length > line.loopFrom && t > line.length) {
    const loops = Math.floor((t - line.loopFrom) / (line.length - line.loopFrom));
    const from = frameAt(line, line.loopFrom), end = frameAt(line, line.length);
    for (const o of offsets) for (const k of [0, 1]) out[o + k] += loops * (end[at + o + k] - from[at + o + k]);
  } else if (t > last[0]) {
    for (const o of offsets) for (const k of [0, 1]) out[o + k] += out[o + 2 + k] * (t - last[0]);
  }
  return out;
}

const IDENTITY: LayerPose['matrix'] = [1, 0, 0, 1, 0, 0];
const FLAT: LayerPose['depth'] = [0, 0];

/**
 * A layer now, or null when it is not drawn: a controller group's layer only while its animation plays
 * (its timeline from that animation's start), a layer whose Animator the controller triggers on its
 * triggered animation's timeline (Idle restarting the default), any other on the illustration's clock;
 * none before its delay. The matrix does not include a bone follower (followMatrix).
 */
export function layerPose(layer: DynamicLayer, clock: LayerClock): LayerPose | null {
  let t: number;
  let line: LayerTimeline | null = layer.animation;
  if (layer.only) {
    if (clock.playing !== layer.only) return null;
    t = clock.animationTime;
  } else if (layer.animation?.states) {
    const state = clock.playing ? layer.animation.states[clock.playing as LayerAnimation] : undefined;
    if (state) line = state;
    t = state || clock.playing === 'Idle' ? clock.animationTime : clock.time;
  } else {
    t = clock.time;
  }
  t -= layer.delay;
  if (t < 0) return null;
  // Wrapped into [0, 1) as the game's shaders do (fract(_Time * speed)): unwrapped, the offset grows
  // without end and a mediump varying loses its precision after a minute (the texture snaps in steps).
  const scrollU = layer.scroll ? wrapUnit(layer.scroll[0] * t) : 0;
  const scrollV = layer.scroll ? wrapUnit(layer.scroll[1] * t) : 0;
  if (!line) {
    const color = layer.color!;
    return { matrix: IDENTITY, depth: FLAT, color, uv: [1, scrollU, 1, scrollV], t, extra: [] };
  }
  const f = frameAt(line, t);
  if (f[10] < 0.5) return null;
  const at = layer.solid ? SOLID_FRAME - 1 : FRAME - 1;
  return {
    matrix: [f[0], f[1], f[2], f[3], f[4], f[5]], depth: layer.solid ? [f[15], f[16]] : FLAT, color: [f[6], f[7], f[8], f[9]],
    uv: [f[11], f[12] + scrollU, f[13], f[14] + scrollV], t, extra: continueOffsets(line, t, f.slice(at), layer.offsets, at),
  };
}

/** The bone a follower reads, as spine-ts gives it. */
export interface FollowedBone { worldX: number; worldY: number; scaleX: number; scaleY: number; getWorldRotationX(): number }

/** The matrix a bone follower puts its layer under: parent x R(rotation) x scale, then the bone's
 *  position, as spine-unity's BoneFollower places its GameObject. */
export function followMatrix(follow: LayerFollow, bone: FollowedBone): LayerPose['matrix'] {
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
  const ra = cos * sx, rb = -sin * sy, rc = sin * sx, rd = cos * sy;
  return [pa * ra + pb * rc, pa * rb + pb * rd, pc * ra + pd * rc, pc * rb + pd * rd,
    follow.xy ? bone.worldX : follow.position[0], follow.xy ? bone.worldY : follow.position[1]];
}

/** outer x inner, for 2x3 affine matrices. */
export function combine(outer: LayerPose['matrix'], inner: LayerPose['matrix']): LayerPose['matrix'] {
  const [a, b, c, d, tx, ty] = outer;
  return [a * inner[0] + b * inner[2], a * inner[1] + b * inner[3], c * inner[0] + d * inner[2], c * inner[1] + d * inner[3],
    a * inner[4] + b * inner[5] + tx, c * inner[4] + d * inner[5] + ty];
}
