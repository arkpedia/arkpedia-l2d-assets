/*
 * The illustration prefab's own mesh layers: the skies, windows, frames and glows the game draws with
 * an animated outfit's skeleton (layers.json in arkpedia/arkpedia-l2d-assets, written by its
 * scripts/layers.py). This module reads and checks that file and works out, frame by frame, what
 * each layer shows: where the skeleton's parts start and end in its draw order, where a layer sits
 * (on its own, on its timeline, or on the bone it follows) and whether it is visible. Drawing them
 * is components/skins/dynamicLayerRenderer.ts's job; nothing here touches WebGL, so the logic is
 * unit tested (scripts/tests/test-dynamic-art.ts). Only DynamicArtwork, which loads when the reader
 * presses Animated, imports it.
 */

/** A time-ordered timeline: frames [t, a, b, c, d, tx, ty, r, g, b, alpha, active, su, ou, sv, ov]. */
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

export interface DynamicLayer {
  name: string;
  blend: 'alpha' | 'add';
  texture: number;
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

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const isObject = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const ANIMATIONS = new Set<LayerAnimation>(['Idle', 'Interact', 'Special', 'Start']);
const WRAPS = new Set(['repeat', 'clamp', 'mirror']);
const FRAME = 16;

function timeline(value: unknown): LayerTimeline | null {
  if (!isObject(value) || !finite(value.length) || value.length < 0 || typeof value.loop !== 'boolean' || !finite(value.loopFrom) || value.loopFrom < 0) return null;
  const frames = value.frames;
  if (!Array.isArray(frames) || !frames.length || frames.length > LIMITS.frames) return null;
  for (let i = 0; i < frames.length; i++) {
    const frame = frames[i];
    if (!Array.isArray(frame) || frame.length !== FRAME || !frame.every(finite) || (i === 0 ? frame[0] !== 0 : frame[0] <= frames[i - 1][0])) return null;
  }
  return { length: value.length, loop: value.loop, loopFrom: value.loopFrom, frames: frames.map((frame: number[]) => frame.slice()) };
}

/** One layer, checked against the file's textures; null when anything about it is not understood. */
function parseLayer(value: unknown, textures: number): DynamicLayer | null {
  if (!isObject(value) || typeof value.name !== 'string' || (value.blend !== 'alpha' && value.blend !== 'add')) return null;
  if (!Number.isSafeInteger(value.texture) || (value.texture as number) < 0 || (value.texture as number) >= textures) return null;
  const { vertices, uvs, colors, triangles } = value;
  if (!Array.isArray(vertices) || vertices.length < 6 || vertices.length % 2 || !vertices.every(finite)) return null;
  const count = vertices.length / 2;
  if (count > 65535 || !Array.isArray(uvs) || uvs.length !== vertices.length || !uvs.every(finite)) return null;
  if (colors !== null && !(Array.isArray(colors) && colors.length === count * 4 && colors.every(finite))) return null;
  if (!Array.isArray(triangles) || !triangles.length || triangles.length % 3 || !triangles.every((t) => Number.isSafeInteger(t) && t >= 0 && t < count)) return null;
  let animation: DynamicLayer['animation'] = null;
  let color: DynamicLayer['color'] = null;
  if (value.animation === null) {
    if (!Array.isArray(value.color) || value.color.length !== 4 || !value.color.every(finite)) return null;
    color = [value.color[0], value.color[1], value.color[2], value.color[3]];
  } else {
    const main = timeline(value.animation);
    if (!main) return null;
    let states: Partial<Record<LayerAnimation, LayerTimeline>> | null = null;
    const raw = (value.animation as Record<string, unknown>).states;
    if (raw !== undefined) {
      if (!isObject(raw)) return null;
      states = {};
      for (const [name, state] of Object.entries(raw)) {
        const parsed = timeline(state);
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
  if (value.scroll !== null && !(Array.isArray(value.scroll) && value.scroll.length === 2 && value.scroll.every(finite))) return null;
  if (value.only !== null && !ANIMATIONS.has(value.only as LayerAnimation)) return null;
  if (!finite(value.delay) || value.delay < 0) return null;
  return {
    name: value.name, blend: value.blend, texture: value.texture as number, color,
    vertices: new Float32Array(vertices), uvs: new Float32Array(uvs), colors: colors ? new Float32Array(colors as number[]) : null,
    triangles: new Uint16Array(triangles), follow, animation,
    scroll: value.scroll ? [(value.scroll as number[])[0], (value.scroll as number[])[1]] : null,
    only: (value.only as LayerAnimation | null) ?? null, delay: value.delay,
  };
}

/** A file name inside the model's folder: never a path. */
const LAYER_FILE = /^layer\d+\.webp$/;

/** Bounds on what one layers.json may ask of the browser (each texture is a fetch and a GPU upload).
 *  The 88 Global models stay far inside them: at most 21 textures, 60 draw entries, a few hundred frames. */
export const LIMITS = { textures: 64, draw: 1024, frames: 20000 };

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
  const textures = value.textures;
  if (!Array.isArray(textures) || textures.length > LIMITS.textures) return fail('textures');
  const parsedTextures = textures.map((texture, i): LayerTexture => {
    if (!isObject(texture) || texture.file !== `layer${i}.webp` || !LAYER_FILE.test(String(texture.file)) || !finite(texture.width) || !finite(texture.height)
      || !Array.isArray(texture.wrap) || texture.wrap.length !== 2 || !texture.wrap.every((w) => WRAPS.has(w as string))) return fail(`textures[${i}]`);
    return { file: texture.file as string, width: texture.width as number, height: texture.height as number, wrap: [texture.wrap[0], texture.wrap[1]] as LayerTexture['wrap'] };
  });
  const bounds = value.bounds;
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
    const layer = isObject(entry) ? parseLayer(entry.layer, parsedTextures.length) : null;
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
  color: [number, number, number, number];
  /** On the exported UVs: u' = u su + ou, v' = v sv + ov (scroll included). */
  uv: [number, number, number, number];
}

const IDENTITY: LayerPose['matrix'] = [1, 0, 0, 1, 0, 0];

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
    return { matrix: IDENTITY, color, uv: [1, scrollU, 1, scrollV] };
  }
  const f = frameAt(line, t);
  if (f[10] < 0.5) return null;
  return { matrix: [f[0], f[1], f[2], f[3], f[4], f[5]], color: [f[6], f[7], f[8], f[9]], uv: [f[11], f[12] + scrollU, f[13], f[14] + scrollV] };
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
