"""The reference simulator for the subset of Unity's built-in ParticleSystem (Shuriken, Unity 2021.3) that
Arknights' animated illustration prefabs use: the specification the site's particle simulator is checked
against (test-only; the export never imports it). The semantics, with the sources each choice rests on,
are written up in the particle research's UNITY-PARTICLES.md, whose section numbers the functions cite.

It reads a system two ways and simulates both the same:
- `Simulation.from_trees(ps_tree, renderer_tree)`: the ParticleSystem / ParticleSystemRenderer typetrees
  exactly as UnityPy returns them (obj.read_typetree());
- `Simulation.from_export(system, document)`: one record of layerParticles.json (scripts/particles.py, the
  format in the README), decoded with the format's own defaults and re-quantized to float32, so a system
  exported correctly simulates exactly as its typetrees do (scripts/tests/test_particles.py and the export
  round trip check that to 1e-9).
It imports nothing outside the standard library and runs without the bundles.

What it is for: a deterministic, readable statement of the semantics that the site's simulator is checked
against (same seed -> same particles), not a bit-exact Unity clone. Unity's own random streams, its noise
field and a few integration details are not public; where this file has to choose, the choice is a named
option on `Options` and the doc says why. The defaults are the site's choices: per-particle random values
hashed from one seed per particle (RANDOM_KEYS, `particle_random`), and a prewarm that simulates only the
last `prewarm_window` of the first loop, at 1/60 s.

    sim = Simulation.from_trees(ps_tree, renderer_tree, seed=1)
    sim.play()
    for _ in range(120):
        sim.step(1 / 60)
    for p in sim.render_records():
        ...   # position (simulation space), size xyz, rotation (radians), rgba, frame, uv rect, custom data

Coordinates are Unity's: left-handed, Y up, an orthographic camera looking down +Z sees X right, Y up.
Angles are radians unless a name says degrees (Shape arc and cone angle are degrees, as serialized).
"""
from __future__ import annotations

import copy
import math
import struct
from dataclasses import dataclass, field

INF = float('inf')
M32 = 0xFFFFFFFF


def f32(x: float) -> float:
    """x rounded to the nearest float32 (Math.fround): every value Unity serializes is one, and so is every
    non-integer number in layerParticles.json once read."""
    return struct.unpack('<f', struct.pack('<f', float(x)))[0]

# ---------------------------------------------------------------------------------------------------------
# Enums as serialized (UNITY-PARTICLES.md section 2)

CURVE_CONSTANT, CURVE_CURVE, CURVE_TWO_CURVES, CURVE_TWO_CONSTANTS = 0, 1, 2, 3
GRAD_COLOR, GRAD_GRADIENT, GRAD_TWO_COLORS, GRAD_TWO_GRADIENTS, GRAD_RANDOM_COLOR = 0, 1, 2, 3, 4
WRAP_PINGPONG, WRAP_REPEAT, WRAP_CLAMP = 0, 1, 2  # AnimationCurve m_PreInfinity / m_PostInfinity
SPACE_LOCAL, SPACE_WORLD, SPACE_CUSTOM = 0, 1, 2  # ParticleSystem.moveWithTransform
SCALING_HIERARCHY, SCALING_LOCAL, SCALING_SHAPE = 0, 1, 2
SHAPE = {'Sphere': 0, 'SphereShell': 1, 'Hemisphere': 2, 'HemisphereShell': 3, 'Cone': 4, 'Box': 5, 'Mesh': 6,
         'ConeShell': 7, 'ConeVolume': 8, 'ConeVolumeShell': 9, 'Circle': 10, 'CircleEdge': 11, 'SingleSidedEdge': 12,
         'MeshRenderer': 13, 'SkinnedMeshRenderer': 14, 'BoxShell': 15, 'BoxEdge': 16, 'Donut': 17, 'Rectangle': 18,
         'Sprite': 19, 'SpriteRenderer': 20}
MULTI_RANDOM, MULTI_LOOP, MULTI_PINGPONG, MULTI_BURST_SPREAD = 0, 1, 2, 3
RENDER_BILLBOARD, RENDER_STRETCH, RENDER_HORIZONTAL, RENDER_VERTICAL, RENDER_MESH, RENDER_NONE = 0, 1, 2, 3, 4, 5
ALIGN_VIEW, ALIGN_WORLD, ALIGN_LOCAL, ALIGN_FACING, ALIGN_VELOCITY = 0, 1, 2, 3, 4
VERTEX_STREAMS = ['Position', 'Normal', 'Tangent', 'Color', 'UV', 'UV2', 'UV3', 'UV4', 'AnimBlend', 'AnimFrame', 'Center',
                  'VertexID', 'SizeX', 'SizeXY', 'SizeXYZ', 'Rotation', 'Rotation3D', 'RotationSpeed', 'RotationSpeed3D',
                  'Velocity', 'Speed', 'AgePercent', 'InvStartLifetime', 'StableRandomX', 'StableRandomXY', 'StableRandomXYZ',
                  'StableRandomXYZW', 'VaryingRandomX', 'VaryingRandomXY', 'VaryingRandomXYZ', 'VaryingRandomXYZW', 'Custom1X',
                  'Custom1XY', 'Custom1XYZ', 'Custom1XYZW', 'Custom2X', 'Custom2XY', 'Custom2XYZ', 'Custom2XYZW', 'NoiseSumX',
                  'NoiseSumXY', 'NoiseSumXYZ', 'NoiseImpulseX', 'NoiseImpulseXY', 'NoiseImpulseXYZ', 'MeshIndex']
# TEXCOORD channels each stream takes (UnityCsReference RendererModuleUI.vertexStreamTexCoordChannels, 2021.3);
# 0 = a fixed attribute (POSITION, NORMAL, TANGENT, COLOR).
STREAM_CHANNELS = [0, 0, 0, 0, 2, 2, 2, 2, 1, 1, 3, 1, 1, 2, 3, 1, 3, 1, 3, 3, 1, 1, 1, 1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4,
                   1, 2, 3, 4, 1, 2, 3, 1, 2, 3, 1]


# ---------------------------------------------------------------------------------------------------------
# Random numbers (section 12). Unity's particle RNG and its per-module seed offsets are not public; this is a
# stand-in with the same shape: one system stream, draws in a fixed order at birth, constants kept per particle.


def mix32(x: int) -> int:
    """A 32-bit integer hash (Wellons' lowbias32): x ^= x >>> 16; x = imul(x, 0x7feb352d); x ^= x >>> 15;
    x = imul(x, 0x846ca68b); x ^= x >>> 16. A bijection on uint32, written so Math.imul reproduces it."""
    x &= M32
    x ^= x >> 16
    x = (x * 0x7FEB352D) & M32
    x ^= x >> 15
    x = (x * 0x846CA68B) & M32
    x ^= x >> 16
    return x


def play_seed(model_seed: int, system_index: int, play_count: int) -> int:
    """The seed of one play of an autoRandomSeed system (a replayed press draws new particles, as the game
    does): mix32(mix32(mix32(model_seed) ^ system_index) ^ play_count), all uint32."""
    return mix32(mix32(mix32(model_seed & M32) ^ (system_index & M32)) ^ (play_count & M32))


def particle_word(seed: int, key: int) -> int:
    """A particle's random 32-bit word for one key (RANDOM_KEYS index): mix32(seed ^ mix32(key + 1))."""
    return mix32((seed ^ mix32(key + 1)) & M32)


def particle_random(seed: int, key: int) -> float:
    """A particle's random value for one key (RANDOM_KEYS index): the top 23 bits of mix32(seed ^ mix32(key + 1)),
    over 8388607, a float in [0, 1] with both ends included, as XorShift128.value."""
    return (mix32((seed ^ mix32(key + 1)) & M32) >> 9) / 8388607.0


class XorShift128:
    """Marsaglia xorshift128, seeded the way UnityEngine.Random.InitState is commonly reconstructed
    (s1 = s0 * 1812433253 + 1, ...). Used here only as a deterministic stand-in."""

    def __init__(self, seed: int):
        m = 0xFFFFFFFF
        self.x = seed & m
        self.y = (self.x * 1812433253 + 1) & m
        self.z = (self.y * 1812433253 + 1) & m
        self.w = (self.z * 1812433253 + 1) & m

    def next_u32(self) -> int:
        m = 0xFFFFFFFF
        t = (self.x ^ (self.x << 11)) & m
        self.x, self.y, self.z = self.y, self.z, self.w
        self.w = (self.w ^ (self.w >> 19) ^ t ^ (t >> 8)) & m
        return self.w

    def value(self) -> float:
        """A float in [0, 1] (both ends inclusive, as Random.value)."""
        return (self.next_u32() & 0x7FFFFF) / 8388607.0

    def range(self, a: float, b: float) -> float:
        return a + (b - a) * self.value()

    def unit_vector(self) -> tuple:
        z = self.range(-1.0, 1.0)
        a = self.range(0.0, 2.0 * math.pi)
        r = math.sqrt(max(0.0, 1.0 - z * z))
        return (r * math.cos(a), r * math.sin(a), z)


class KeyedStream(XorShift128):
    """The draws one particle's shape sampling makes, each `particle_random(seed, first + n)` for the n-th
    draw: the same interface as XorShift128 (value, range, unit_vector), so sample_shape is shared, but the
    values depend only on the particle's seed and the draw's position within the shape."""

    def __init__(self, seed: int, first: int, count: int):  # noqa: D107 - no XorShift state
        self.seed, self.first, self.count, self.n = seed & M32, first, count, 0

    def next_u32(self) -> int:
        raise TypeError('a keyed stream draws values, not words')

    def value(self) -> float:
        if self.n >= self.count:
            raise RuntimeError(f'a shape drew more than the {self.count} keyed values it has')
        v = particle_random(self.seed, self.first + self.n)
        self.n += 1
        return v


# ---------------------------------------------------------------------------------------------------------
# AnimationCurve (section 3.2): cubic Hermite per segment, slopes x segment length, +inf slope = stepped,
# wrap modes outside the key range. No weighted keys exist in these bundles (all weightedMode 0).


def _repeat(t: float, length: float) -> float:
    if length <= 0:
        return 0.0
    return min(max(t - math.floor(t / length) * length, 0.0), length)


def _ping_pong(t: float, length: float) -> float:
    t = _repeat(t, length * 2.0)
    return length - abs(t - length)


def evaluate_curve(keys: list, t: float, pre: int = WRAP_CLAMP, post: int = WRAP_CLAMP) -> float:
    """Unity AnimationCurve.Evaluate for unweighted keys ({'time','value','inSlope','outSlope',...})."""
    if not keys:
        return 0.0
    if len(keys) == 1:
        return keys[0]['value']
    beg, end = keys[0]['time'], keys[-1]['time']
    if t < beg:
        if pre == WRAP_CLAMP:
            t = beg
        elif pre == WRAP_PINGPONG:
            t = beg + _ping_pong(t - beg, end - beg)
        else:
            t = beg + _repeat(t - beg, end - beg)
    elif t > end:
        if post == WRAP_CLAMP:
            t = end
        elif post == WRAP_PINGPONG:
            t = beg + _ping_pong(t - beg, end - beg)
        else:
            t = beg + _repeat(t - beg, end - beg)
    # lhs = last key with time <= t (the right one wins at an exact key time, as Unity's search does)
    i = 0
    while i + 1 < len(keys) - 1 and keys[i + 1]['time'] <= t:
        i += 1
    lhs, rhs = keys[i], keys[i + 1]
    if not math.isfinite(lhs['outSlope']) or not math.isfinite(rhs['inSlope']):
        return lhs['value']  # stepped segment holds the left value
    dx = rhs['time'] - lhs['time']
    if dx == 0:
        return lhs['value']
    s = (t - lhs['time']) / dx
    m0, m1 = lhs['outSlope'] * dx, rhs['inSlope'] * dx
    s2, s3 = s * s, s * s * s
    return ((2 * s3 - 3 * s2 + 1) * lhs['value'] + (s3 - 2 * s2 + s) * m0 + (s3 - s2) * m1 + (-2 * s3 + 3 * s2) * rhs['value'])


def curve_bounds(keys: list) -> tuple[float, float]:
    """(lowest, highest) value a curve takes between its first and last key: every key value and every
    extremum of a segment's cubic inside the segment (a stepped segment holds its left value). The
    export's bounds (maxAlive, prewarmWindow, whether a system emits) use the same definition."""
    if not keys:
        return 0.0, 0.0
    values = [k['value'] for k in keys]
    for lhs, rhs in zip(keys, keys[1:]):
        dx = rhs['time'] - lhs['time']
        if dx <= 0 or not math.isfinite(lhs['outSlope']) or not math.isfinite(rhs['inSlope']):
            continue
        p0, p1, m0, m1 = lhs['value'], rhs['value'], lhs['outSlope'] * dx, rhs['inSlope'] * dx
        # value(s) = a s^3 + b s^2 + c s + p0 on 0 < s < 1
        a = 2 * p0 + m0 + m1 - 2 * p1
        b = -3 * p0 - 2 * m0 - m1 + 3 * p1
        c = m0
        roots = []
        if abs(a) > 1e-12:
            disc = b * b - 3 * a * c
            if disc >= 0:
                r = math.sqrt(disc)
                roots = [(-b - r) / (3 * a), (-b + r) / (3 * a)]
        elif abs(b) > 1e-12:
            roots = [-c / (2 * b)]
        for root in roots:
            if 0 < root < 1:
                values.append(((a * root + b) * root + c) * root + p0)
    return min(values), max(values)


@dataclass
class Curve:
    keys: list
    pre: int = WRAP_CLAMP
    post: int = WRAP_CLAMP

    @classmethod
    def from_tree(cls, tree: dict | None) -> 'Curve':
        tree = tree or {}
        return cls(list(tree.get('m_Curve') or []), tree.get('m_PreInfinity', WRAP_CLAMP), tree.get('m_PostInfinity', WRAP_CLAMP))

    @classmethod
    def from_export(cls, keys: list, pre: int = WRAP_CLAMP, post: int = WRAP_CLAMP) -> 'Curve':
        """Flat [t, v, in, out, ...] keys (a stepped slope is null)."""
        if len(keys) % 4:
            raise ValueError('curve keys come in fours')
        out = []
        for i in range(0, len(keys), 4):
            t, v, i_, o = keys[i:i + 4]
            out.append({'time': f32(t), 'value': f32(v), 'inSlope': INF if i_ is None else f32(i_), 'outSlope': INF if o is None else f32(o)})
        return cls(out, pre, post)

    def __call__(self, t: float) -> float:
        return evaluate_curve(self.keys, t, self.pre, self.post)


@dataclass
class MinMaxCurve:
    """Serialized {minMaxState, scalar, minScalar, maxCurve, minCurve} (section 3.1)."""
    state: int = CURVE_CONSTANT
    scalar: float = 0.0
    min_scalar: float = 0.0
    max_curve: Curve = field(default_factory=lambda: Curve([]))
    min_curve: Curve = field(default_factory=lambda: Curve([]))

    @classmethod
    def from_tree(cls, tree) -> 'MinMaxCurve':
        if isinstance(tree, MinMaxCurve):
            return tree  # already read (from_export builds its modules from parsed values)
        if not tree:
            return cls()
        return cls(tree.get('minMaxState', 0), float(tree.get('scalar', 0.0)), float(tree.get('minScalar', 0.0)),
                   Curve.from_tree(tree.get('maxCurve')), Curve.from_tree(tree.get('minCurve')))

    @classmethod
    def from_export(cls, value) -> 'MinMaxCurve':
        """A layerParticles.json curve value: a number; ["r", min, max]; ["c", scalar, keys] or ["cc", scalar,
        minKeys, maxKeys], each optionally followed by ["w", pre, post] (["w", minPre, minPost, maxPre,
        maxPost] for two curves) when a wrap is not Clamp. In the curve modes minScalar is not read."""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            v = f32(value)
            return cls(CURVE_CONSTANT, v, v)
        tag = value[0]
        if tag == 'r':
            return cls(CURVE_TWO_CONSTANTS, f32(value[2]), f32(value[1]))
        if tag == 'c':
            wrap = _wrap_of(value[3:], 2)
            return cls(CURVE_CURVE, f32(value[1]), f32(value[1]), Curve.from_export(value[2], *wrap[:2]))
        if tag == 'cc':
            wrap = _wrap_of(value[4:], 4)
            return cls(CURVE_TWO_CURVES, f32(value[1]), f32(value[1]), Curve.from_export(value[3], *wrap[2:]),
                       Curve.from_export(value[2], *wrap[:2]))
        raise ValueError(f'unknown curve value {tag!r}')

    @classmethod
    def constant(cls, v: float) -> 'MinMaxCurve':
        return cls(CURVE_CONSTANT, v, v)

    @property
    def is_random(self) -> bool:
        return self.state in (CURVE_TWO_CURVES, CURVE_TWO_CONSTANTS)

    def evaluate(self, t: float, lerp: float = 1.0) -> float:
        """t: the curve's x (normalized system time for start values, age/lifetime over lifetime);
        lerp: the random factor (a per-particle constant for over-lifetime modules)."""
        if self.state == CURVE_CONSTANT:
            return self.scalar
        if self.state == CURVE_TWO_CONSTANTS:
            return self.min_scalar + (self.scalar - self.min_scalar) * lerp
        if self.state == CURVE_CURVE:
            return self.max_curve(t) * self.scalar
        lo, hi = self.min_curve(t), self.max_curve(t)
        return (lo + (hi - lo) * lerp) * self.scalar  # TwoCurves: both curves x scalar (minScalar unused)

    def bounds(self) -> tuple[float, float]:
        """(lowest, highest) value over any x and random factor (curve_bounds for the curve modes)."""
        if self.state == CURVE_CONSTANT:
            return self.scalar, self.scalar
        if self.state == CURVE_TWO_CONSTANTS:
            return min(self.scalar, self.min_scalar), max(self.scalar, self.min_scalar)
        curves = [self.max_curve] + ([self.min_curve] if self.state == CURVE_TWO_CURVES else [])
        lows, highs = zip(*(curve_bounds(c.keys) for c in curves))
        a, b = min(lows) * self.scalar, max(highs) * self.scalar
        return min(a, b), max(a, b)

    def max_value(self) -> float:
        return self.bounds()[1]


def _wrap_of(tail: list, count: int) -> list:
    """The wrap modes of a curve value's optional trailing ["w", ...] (Clamp when absent)."""
    if not tail:
        return [WRAP_CLAMP] * count
    if tail[0][0] != 'w' or len(tail[0]) != count + 1:
        raise ValueError('a curve value ends with ["w", ...] or nothing')
    return [WRAPS[name] for name in tail[0][1:]]


WRAPS = {'pingPong': WRAP_PINGPONG, 'repeat': WRAP_REPEAT, 'clamp': WRAP_CLAMP}


@dataclass
class Gradient:
    """Serialized Gradient: key0..key7 (rgba), ctime0..7 / atime0..7 (uint16, /65535), m_NumColorKeys,
    m_NumAlphaKeys, m_Mode (0 Blend, 1 Fixed). Section 3.3. Key times are float32(time / 65535), as a
    float32 engine has them."""
    colors: list  # [(t, (r, g, b))]
    alphas: list  # [(t, a)]
    mode: int = 0

    @classmethod
    def from_tree(cls, tree: dict | None) -> 'Gradient':
        tree = tree or {}
        nc = tree.get('m_NumColorKeys', 2)
        na = tree.get('m_NumAlphaKeys', 2)
        colors, alphas = [], []
        for i in range(8):
            k = tree.get(f'key{i}') or {'r': 1.0, 'g': 1.0, 'b': 1.0, 'a': 1.0}
            if i < nc:
                colors.append((f32(tree.get(f'ctime{i}', 0) / 65535.0), (k['r'], k['g'], k['b'])))
            if i < na:
                alphas.append((f32(tree.get(f'atime{i}', 0) / 65535.0), k['a']))
        return cls(colors or [(0.0, (1.0, 1.0, 1.0))], alphas or [(0.0, 1.0)], tree.get('m_Mode', 0))

    @classmethod
    def from_export(cls, g: dict) -> 'Gradient':
        """{c: [t, r, g, b, ...], a: [t, a, ...], fixed}: times already divided by 65535."""
        c, a = g['c'], g['a']
        colors = [(f32(c[i]), (f32(c[i + 1]), f32(c[i + 2]), f32(c[i + 3]))) for i in range(0, len(c), 4)]
        alphas = [(f32(a[i]), f32(a[i + 1])) for i in range(0, len(a), 2)]
        return cls(colors or [(0.0, (1.0, 1.0, 1.0))], alphas or [(0.0, 1.0)], 1 if g.get('fixed') else 0)

    @staticmethod
    def _eval(keys, t, mode, lerp):
        if mode == 1:  # Fixed: the first key whose time is greater than t
            for kt, v in keys:
                if kt > t:
                    return v
            return keys[-1][1]
        if t <= keys[0][0]:
            return keys[0][1]
        if t >= keys[-1][0]:
            return keys[-1][1]
        for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
            if t0 <= t <= t1:
                f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
                return lerp(v0, v1, f)
        return keys[-1][1]

    def evaluate(self, t: float) -> tuple:
        t = min(max(t, 0.0), 1.0)
        c = self._eval(self.colors, t, self.mode, lambda a, b, f: tuple(x + (y - x) * f for x, y in zip(a, b)))
        a = self._eval(self.alphas, t, self.mode, lambda a, b, f: a + (b - a) * f)
        return (c[0], c[1], c[2], a)


@dataclass
class MinMaxGradient:
    """Serialized {minMaxState, minColor, maxColor, minGradient, maxGradient}. Section 3.3."""
    state: int = GRAD_COLOR
    min_color: tuple = (1.0, 1.0, 1.0, 1.0)
    max_color: tuple = (1.0, 1.0, 1.0, 1.0)
    min_gradient: Gradient | None = None
    max_gradient: Gradient | None = None

    @classmethod
    def from_tree(cls, tree) -> 'MinMaxGradient':
        if isinstance(tree, MinMaxGradient):
            return tree  # already read (from_export)
        if not tree:
            return cls()

        def col(c):
            c = c or {}
            return (c.get('r', 1.0), c.get('g', 1.0), c.get('b', 1.0), c.get('a', 1.0))

        return cls(tree.get('minMaxState', 0), col(tree.get('minColor')), col(tree.get('maxColor')),
                   Gradient.from_tree(tree.get('minGradient')), Gradient.from_tree(tree.get('maxGradient')))

    @classmethod
    def from_export(cls, value) -> 'MinMaxGradient':
        """A layerParticles.json colour value: [r, g, b, a]; ["r", c0, c1]; ["g", G]; ["gg", G0, G1]; ["rg", G]."""
        if not isinstance(value[0], str):
            return cls(GRAD_COLOR, max_color=tuple(f32(x) for x in value))
        tag = value[0]
        if tag == 'r':
            return cls(GRAD_TWO_COLORS, tuple(f32(x) for x in value[1]), tuple(f32(x) for x in value[2]))
        if tag == 'g':
            return cls(GRAD_GRADIENT, max_gradient=Gradient.from_export(value[1]), min_gradient=Gradient.from_export(value[1]))
        if tag == 'gg':
            return cls(GRAD_TWO_GRADIENTS, min_gradient=Gradient.from_export(value[1]), max_gradient=Gradient.from_export(value[2]))
        if tag == 'rg':
            return cls(GRAD_RANDOM_COLOR, max_gradient=Gradient.from_export(value[1]), min_gradient=Gradient.from_export(value[1]))
        raise ValueError(f'unknown colour value {tag!r}')

    @property
    def is_random(self) -> bool:
        return self.state in (GRAD_TWO_COLORS, GRAD_TWO_GRADIENTS, GRAD_RANDOM_COLOR)

    def evaluate(self, t: float, lerp: float = 1.0) -> tuple:
        if self.state == GRAD_COLOR:
            return self.max_color
        if self.state == GRAD_GRADIENT:
            return self.max_gradient.evaluate(t)
        if self.state == GRAD_TWO_COLORS:
            return tuple(a + (b - a) * lerp for a, b in zip(self.min_color, self.max_color))
        if self.state == GRAD_TWO_GRADIENTS:
            a, b = self.min_gradient.evaluate(t), self.max_gradient.evaluate(t)
            return tuple(x + (y - x) * lerp for x, y in zip(a, b))
        return self.max_gradient.evaluate(lerp)  # RandomColor: the gradient sampled at a random time


def color32(c: tuple) -> tuple:
    """Particle.startColor is a Color32: quantize to 8 bits per channel (section 4.1)."""
    return tuple(min(255, max(0, int(round(x * 255.0)))) / 255.0 for x in c)


# ---------------------------------------------------------------------------------------------------------
# Small vector / quaternion helpers (Unity conventions: Euler applied Z, then X, then Y)


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def v_hadamard(a, b):
    return (a[0] * b[0], a[1] * b[1], a[2] * b[2])


def v_len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def v_norm(a, fallback=(0.0, 0.0, 1.0)):
    n = v_len(a)
    return fallback if n < 1e-12 else (a[0] / n, a[1] / n, a[2] / n)


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def v_lerp(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def orbit_velocity(omega, rel, h: float):
    """Orbital velocity (rad/s about each axis; `rel` the particle's offset from the orbit's centre) as the velocity
    that turns `rel` about `omega` by exactly |omega| h over a step of h seconds (Rodrigues): (R rel - rel) / h, so
    the radius holds. Adding cross(omega, rel) and integrating it explicitly instead grows the radius by
    sqrt(1 + (|omega| h)^2) every step: Hoshiguma's fire_ring_ctrl went from 3.3 to 19 Unity units in 3 s, where both
    recordings keep the ring on the shield. A step of 0 takes the limit, cross(omega, rel)."""
    w = v_len(omega)
    if h == 0.0 or w == 0.0:
        return v_cross(omega, rel)
    kx, ky, kz = omega[0] / w, omega[1] / w, omega[2] / w
    angle = w * h
    c, s = math.cos(angle), math.sin(angle)
    rx, ry, rz = rel
    kd = kx * rx + ky * ry + kz * rz
    tx = rx * c + (ky * rz - kz * ry) * s + kx * kd * (1.0 - c)
    ty = ry * c + (kz * rx - kx * rz) * s + ky * kd * (1.0 - c)
    tz = rz * c + (kx * ry - ky * rx) * s + kz * kd * (1.0 - c)
    return ((tx - rx) / h, (ty - ry) / h, (tz - rz) / h)


def q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def q_axis_angle(axis, rad):
    s = math.sin(rad / 2)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(rad / 2))


def q_euler_deg(x, y, z):
    """Quaternion.Euler(x, y, z): rotation about Z, then X, then Y (q = qy * qx * qz)."""
    qx = q_axis_angle((1, 0, 0), math.radians(x))
    qy = q_axis_angle((0, 1, 0), math.radians(y))
    qz = q_axis_angle((0, 0, 1), math.radians(z))
    return q_mul(q_mul(qy, qx), qz)


def q_rotate(q, v):
    x, y, z, w = q
    u = (x, y, z)
    t = v_mul(v_cross(u, v), 2.0)
    return v_add(v_add(v, v_mul(t, w)), v_cross(u, t))


def q_conj(q):
    return (-q[0], -q[1], -q[2], q[3])


@dataclass
class Pose:
    """An emitter's placement in the space the viewer draws in ("world": the prefab root, section 6).
    rotation is a quaternion (x, y, z, w); scale is what the scaling mode keeps (section 6.2). `linear`, when
    set, is the emitter matrix's 3x3 part (row-major, 9 numbers) and replaces rotation x scale for points and
    vectors: a Hierarchy emitter under a non-uniformly scaled, rotated parent is skewed, which no
    rotation and scale reproduce. Module vectors given in the other space still turn by `rotation` alone."""
    position: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (0.0, 0.0, 0.0, 1.0)
    scale: tuple = (1.0, 1.0, 1.0)
    linear: tuple | None = None

    @classmethod
    def from_export(cls, emitter: dict) -> 'Pose':
        """A static emitter of layerParticles.json: {matrix (3x4, row-major), rotation, scale}."""
        m = [f32(x) for x in emitter['matrix']]
        return cls((m[3], m[7], m[11]), tuple(f32(x) for x in emitter['rotation']), (1.0, 1.0, 1.0),
                   (m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]))

    def point(self, p):
        if self.linear is not None:
            return v_add(_mat_vec(self.linear, p), self.position)
        return v_add(q_rotate(self.rotation, v_hadamard(p, self.scale)), self.position)

    def vector(self, v):
        if self.linear is not None:
            return _mat_vec(self.linear, v)
        return q_rotate(self.rotation, v_hadamard(v, self.scale))

    def inverse_vector(self, v):
        if self.linear is not None:
            return _solve3(self.linear, v)
        r = q_rotate(q_conj(self.rotation), v)
        return tuple(c / s if s else 0.0 for c, s in zip(r, self.scale))

    def inverse_point(self, p):
        return self.inverse_vector(v_sub(p, self.position))


def _mat_vec(m, v):
    return (m[0] * v[0] + m[1] * v[1] + m[2] * v[2], m[3] * v[0] + m[4] * v[1] + m[5] * v[2], m[6] * v[0] + m[7] * v[1] + m[8] * v[2])


def _solve3(m, v):
    """x with m x = v (Cramer's rule); 0 for a degenerate matrix, as a zero scale component gives."""
    det = m[0] * (m[4] * m[8] - m[5] * m[7]) - m[1] * (m[3] * m[8] - m[5] * m[6]) + m[2] * (m[3] * m[7] - m[4] * m[6])
    if abs(det) < 1e-30:
        return (0.0, 0.0, 0.0)
    cols = [(m[0], m[3], m[6]), (m[1], m[4], m[7]), (m[2], m[5], m[8])]
    out = []
    for i in range(3):
        c = list(cols)
        c[i] = v
        a = [c[0][0], c[1][0], c[2][0], c[0][1], c[1][1], c[2][1], c[0][2], c[1][2], c[2][2]]
        out.append((a[0] * (a[4] * a[8] - a[5] * a[7]) - a[1] * (a[3] * a[8] - a[5] * a[6]) + a[2] * (a[3] * a[7] - a[4] * a[6])) / det)
    return tuple(out)


def emitter_pose(scaling_mode: int, world_position, world_rotation, local_scale, lossy_scale) -> tuple['Pose', tuple]:
    """(pose for positions and velocities, scale applied to particle sizes) for a scaling mode (section 6.2).
    Hierarchy: the full lossy scale for both. Local: only the system's own m_LocalScale (parents ignored).
    Shape: the lossy scale moves start positions only; sizes are unscaled."""
    if scaling_mode == SCALING_LOCAL:
        return Pose(world_position, world_rotation, local_scale), local_scale
    if scaling_mode == SCALING_SHAPE:
        return Pose(world_position, world_rotation, lossy_scale), (1.0, 1.0, 1.0)
    return Pose(world_position, world_rotation, lossy_scale), lossy_scale


# ---------------------------------------------------------------------------------------------------------
# Options: every place where Unity's exact behaviour is not public (section 12)


@dataclass
class Options:
    gravity: tuple = (0.0, -9.81, 0.0)          # Physics.gravity default; the game may change it (unverified)
    # Where every rate-over-time accumulator starts (a system's at each play, a sub-emitter link's per parent
    # particle), 0..1: the fraction of the way to the first birth. 0: the first rate particle after 1/rate s;
    # 1: one at once, on the first step (inferred; P6 settles it on the recordings).
    emit_accumulator_start: float = 0.0
    # Limit velocity keeps (1 - dampen)^(dt * fps) of a speed's excess over a step of dt; 0 applies `dampen` once per
    # step whatever its length. 60: once per frame at the game's 60 fps (the recordings' rate), and the same decay when
    # a reader steps at 1/30 s or a newborn particle's first part-step is shorter (P6: the recordings cannot tell R
    # apart, X6's sparks look the same under 30, 60 and 120).
    dampen_reference_fps: float = 60.0
    dampen_excess_only: bool = True              # True: |v| -> L + (|v|-L)(1-d); False: max(L, |v|(1-d)) (Cocos)
    prewarm_step: float = 1.0 / 60.0             # step used to simulate one loop for prewarm (Unity's is internal; the site's h)
    # 'window': prewarm simulates only the last Config.prewarm_window of the first loop, which every particle
    # alive at the loop's end was born in (section 3.4 of the plan); 'full': the whole first loop.
    prewarm: str = 'window'
    # 'hashed': the system's generator draws only system-level values (start delay, a random rate each step,
    # burst rolls) and one 32-bit seed per particle at its birth; every per-particle value is
    # particle_random(seed, RANDOM_KEYS index), so values never depend on which other draws happened.
    # 'stream': every value drawn in turn from the system's generator (RAND_KEYS then the start values).
    randoms: str = 'hashed'
    subframe_emission: bool = True               # spread a step's new particles over the step (documented-ish)
    noise_enabled: bool = True                   # the noise field is an approximation (section 7.6)
    texture_frame_mode: str = 'evaluate-cycled'  # 'evaluate-cycled' (fo(frac(a*cycles))) or 'cocos' (frac(cycles*fo(a)))


# ---------------------------------------------------------------------------------------------------------
# Configuration parsed from the typetrees


def _vec(d, default=(0.0, 0.0, 0.0)):
    if not d:
        return default
    return (float(d.get('x', 0.0)), float(d.get('y', 0.0)), float(d.get('z', 0.0)))


@dataclass
class Burst:
    time: float
    count: MinMaxCurve
    cycles: int  # 0 = infinite (repeat every interval until the end of the loop)
    interval: float
    probability: float


@dataclass
class Config:
    duration: float
    looping: bool
    prewarm: bool
    start_delay: MinMaxCurve
    simulation_speed: float
    space: int
    scaling_mode: int
    max_particles: int
    auto_random_seed: bool
    random_seed: int
    start_lifetime: MinMaxCurve
    start_speed: MinMaxCurve
    start_size: tuple  # (x, y, z) MinMaxCurves
    size3d: bool
    start_rotation: tuple  # (x, y, z)
    rotation3d: bool
    flip_rotation: float
    start_color: MinMaxGradient
    gravity_modifier: MinMaxCurve
    emission_enabled: bool
    rate_over_time: MinMaxCurve
    rate_over_distance: MinMaxCurve
    bursts: list
    shape: dict | None
    modules: dict
    renderer: dict | None
    # The prewarm's window (prewarm_window()), None unless the system prewarms and loops.
    prewarm_window: float | None = None
    # A mesh shape's mesh (mesh_shape_data), or None.
    shape_mesh: dict | None = None

    @classmethod
    def from_trees(cls, ps: dict, renderer: dict | None = None, child_lifetime: float = 0.0, mesh_of=None) -> 'Config':
        """`child_lifetime`: the longest life of its sub-emitter children's particles (which lengthens the
        prewarm window); `mesh_of(reference)`: a mesh shape's mesh as {vertices, normals, submeshes, colors}
        (layers.bundle_readers' mesh_of), needed only for a mesh shape."""
        config = cls._read(ps, renderer)
        config.prewarm_window = prewarm_window(config, child_lifetime)
        if config.shape and config.shape.get('type') == SHAPE['Mesh']:
            if mesh_of is None:
                raise ValueError('a mesh shape needs mesh_of')
            mesh = mesh_of(config.shape['m_Mesh'])
            config.shape_mesh = mesh_shape_data(mesh['vertices'], mesh['normals'], mesh['submeshes'][0], mesh.get('colors'))
        return config

    @classmethod
    def from_export(cls, system: dict, document: dict) -> 'Config':
        """One record of layerParticles.json's `systems` (with its document, for meshes and materials), read
        back into the typetree shapes from_trees reads; its prewarm window is the one the export wrote."""
        ps, renderer = trees_from_export(system, document)
        config = cls._read(ps, renderer)
        if config.shape and config.shape.get('type') == SHAPE['Mesh']:
            m = document['meshes'][system['shape']['mesh']]
            group = lambda values, n: [values[i:i + n] for i in range(0, len(values), n)]  # noqa: E731
            config.shape_mesh = mesh_shape_data(group(m['vertices'], 3), group(m['normals'], 3), m['triangles'],
                                                group(m['colors'], 4) if m['colors'] else None)
            if [f32(x) for x in m['areaCdf']] != config.shape_mesh['cdf']:
                raise ValueError(f'{system["name"]}: the mesh shape\'s areaCdf is not its triangles\' areas')
        clock = system['clock']
        config.prewarm_window = f32(clock['prewarmWindow']) if 'prewarmWindow' in clock else None
        if (config.prewarm_window is None) != (prewarm_window(config, 0.0) is None):
            raise ValueError(f'{system["name"]}: prewarmWindow must be written exactly when the system prewarms and loops')
        return config

    @classmethod
    def _read(cls, ps: dict, renderer: dict | None) -> 'Config':
        init = ps['InitialModule']
        em = ps['EmissionModule']
        sh = ps['ShapeModule']
        mods = {}
        for name in ('SizeModule', 'RotationModule', 'ColorModule', 'UVModule', 'VelocityModule', 'ClampVelocityModule',
                     'ForceModule', 'NoiseModule', 'InheritVelocityModule', 'CustomDataModule', 'TrailModule', 'SubModule'):
            m = ps.get(name) or {}
            if m.get('enabled'):
                mods[name] = m
        bursts = []
        for b in em.get('m_Bursts') or []:
            bursts.append(Burst(float(b['time']), MinMaxCurve.from_tree(b['countCurve']), int(b.get('cycleCount', 1)),
                                float(b.get('repeatInterval', 0.01)), float(b.get('probability', 1.0))))
        return cls(
            duration=float(ps['lengthInSec']), looping=bool(ps['looping']), prewarm=bool(ps['prewarm']),
            start_delay=MinMaxCurve.from_tree(ps.get('startDelay')), simulation_speed=float(ps.get('simulationSpeed', 1.0)),
            space=int(ps.get('moveWithTransform', 0)), scaling_mode=int(ps.get('scalingMode', 1)),
            max_particles=int(init.get('maxNumParticles', 1000)), auto_random_seed=bool(ps.get('autoRandomSeed', True)),
            random_seed=int(ps.get('randomSeed', 0)) & M32,
            start_lifetime=MinMaxCurve.from_tree(init['startLifetime']), start_speed=MinMaxCurve.from_tree(init['startSpeed']),
            start_size=(MinMaxCurve.from_tree(init['startSize']), MinMaxCurve.from_tree(init['startSizeY']),
                        MinMaxCurve.from_tree(init['startSizeZ'])),
            size3d=bool(init.get('size3D')),
            start_rotation=(MinMaxCurve.from_tree(init['startRotationX']), MinMaxCurve.from_tree(init['startRotationY']),
                            MinMaxCurve.from_tree(init['startRotation'])),
            rotation3d=bool(init.get('rotation3D')), flip_rotation=float(init.get('randomizeRotationDirection', 0.0)),
            start_color=MinMaxGradient.from_tree(init['startColor']),
            gravity_modifier=MinMaxCurve.from_tree(init.get('gravityModifier')),
            emission_enabled=bool(em.get('enabled')), rate_over_time=MinMaxCurve.from_tree(em['rateOverTime']),
            rate_over_distance=MinMaxCurve.from_tree(em['rateOverDistance']), bursts=bursts,
            shape=sh if sh.get('enabled') else None, modules=mods, renderer=renderer)


PREWARM_CAP = 30.0  # seconds: the window when maxParticles could bind


def burst_fires(b: Burst, duration: float) -> int:
    """How many times a burst fires in one loop (as _emission_events counts them)."""
    fires, k = 0, 0
    while not (b.cycles and k >= b.cycles):
        if b.time + k * b.interval >= duration:
            break
        fires += 1
        if b.interval <= 0:
            break
        k += 1
    return fires


def prewarm_window(c: Config, child_lifetime: float) -> float | None:
    """The prewarm window W (seconds of system time), None unless the system prewarms and loops: the
    longest a particle can live (the start lifetime's highest value, plus a sub-emitter child's), within one
    loop, so prewarming only the loop's last W gives every particle alive at its end exactly. Where
    maxParticles could bind (the highest rate over W plus every burst of a loop at its highest count is
    more than maxParticles), dropped particles depend on what came earlier: W = min(duration, PREWARM_CAP).
    float32, as the export writes it."""
    if not (c.prewarm and c.looping):
        return None
    duration = max(c.duration, 0.0)
    w = min(duration, max(0.0, c.start_lifetime.max_value()) + child_lifetime)
    bursts = sum(max(0, int(math.floor(b.count.max_value() + 0.5))) * burst_fires(b, duration) for b in c.bursts)
    if max(0.0, c.rate_over_time.max_value()) * w + bursts > c.max_particles:
        w = min(duration, PREWARM_CAP)
    return f32(w)


# ---------------------------------------------------------------------------------------------------------
# Shape module (section 5): a start position and a unit direction in the shape's space, then the shape TRS


def _arc_fraction(rng: XorShift128, multi: dict, state: dict, key: str, burst_index, burst_size) -> float:
    """A position along an arc / edge as a fraction 0..1, for the Random / Loop / PingPong / BurstSpread modes
    and the spread quantization (section 5.3)."""
    mode = multi.get('mode', MULTI_RANDOM)
    spread = float(multi.get('spread', 0.0))
    if mode == MULTI_RANDOM:
        f = rng.value()
    elif mode == MULTI_BURST_SPREAD:
        f = (burst_index / burst_size) if burst_size else rng.value()
    else:
        f = state.get(key, 0.0) % 1.0 if mode == MULTI_LOOP else _ping_pong(state.get(key, 0.0), 1.0)
    if spread > 0:
        f = math.floor(f / spread) * spread
    return min(max(f, 0.0), 1.0)


def sample_shape(shape: dict | None, rng: XorShift128, state: dict, sys_t01: float, burst_index=None, burst_size=0, mesh=None, colour_out=None):
    """(position, direction) of one new particle in emitter space (before the emitter pose). `mesh`: a mesh
    shape's mesh (mesh_shape_data); a vertex colour it gives the particle is appended to `colour_out`."""
    if not shape:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)
    kind = shape['type']
    radius = float(shape['radius']['value'])
    thickness = float(shape.get('radiusThickness', 1.0))
    arc = math.radians(float(shape['arc']['value']))
    pos, d = (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)

    def radial(power):  # uniform over the shell between r(1 - thickness) and r
        inner = (1.0 - thickness) ** power
        return (inner + (1.0 - inner) * rng.value()) ** (1.0 / power)

    if kind in (SHAPE['Sphere'], SHAPE['SphereShell'], SHAPE['Hemisphere'], SHAPE['HemisphereShell']):
        u = rng.unit_vector()
        if kind in (SHAPE['Hemisphere'], SHAPE['HemisphereShell']):
            u = (u[0], u[1], abs(u[2]))
        r = radius if kind in (SHAPE['SphereShell'], SHAPE['HemisphereShell']) else radius * radial(3)
        pos, d = v_mul(u, r), u
    elif kind in (SHAPE['Circle'], SHAPE['CircleEdge']):
        a = arc * _arc_fraction(rng, shape['arc'], state, 'arc', burst_index, burst_size)
        u = (math.cos(a), math.sin(a), 0.0)
        r = radius if kind == SHAPE['CircleEdge'] else radius * radial(2)
        pos, d = v_mul(u, r), u
    elif kind in (SHAPE['Cone'], SHAPE['ConeShell'], SHAPE['ConeVolume'], SHAPE['ConeVolumeShell']):
        a = arc * _arc_fraction(rng, shape['arc'], state, 'arc', burst_index, burst_size)
        u = (math.cos(a), math.sin(a), 0.0)
        shell = kind in (SHAPE['ConeShell'], SHAPE['ConeVolumeShell'])
        rn = 1.0 if shell else radial(2)
        tan = math.tan(math.radians(min(max(float(shape.get('angle', 25.0)), 0.0), 90.0)))
        base = v_mul(u, rn * radius)
        d = v_norm((u[0] * rn * tan, u[1] * rn * tan, 1.0))  # through the cone's virtual apex (inferred from the gizmo)
        pos = base
        if kind in (SHAPE['ConeVolume'], SHAPE['ConeVolumeShell']):
            along = rng.value() * float(shape.get('length', 5.0))
            pos = v_add(base, v_mul(d, along / max(d[2], 1e-6)))
    elif kind in (SHAPE['Box'], SHAPE['BoxShell'], SHAPE['BoxEdge']):
        p = [rng.range(-0.5, 0.5) for _ in range(3)]
        if kind != SHAPE['Box']:
            th = _vec(shape.get('boxThickness'))
            faces = [0, 1, 2] if kind == SHAPE['BoxShell'] else None
            if faces:  # push one random axis to a face, within the thickness
                axis = int(rng.value() * 2.999)
                side = 0.5 if rng.value() >= 0.5 else -0.5
                p[axis] = side - math.copysign(rng.value() * th[axis] * 0.5, side)
            else:  # edge: two axes on faces
                free = int(rng.value() * 2.999)
                for axis in range(3):
                    if axis != free:
                        side = 0.5 if rng.value() >= 0.5 else -0.5
                        p[axis] = side - math.copysign(rng.value() * th[axis] * 0.5, side)
        pos, d = tuple(p), (0.0, 0.0, 1.0)
    elif kind == SHAPE['Rectangle']:
        pos, d = (rng.range(-0.5, 0.5), rng.range(-0.5, 0.5), 0.0), (0.0, 0.0, 1.0)
    elif kind == SHAPE['SingleSidedEdge']:
        f = _arc_fraction(rng, shape['radius'], state, 'radius', burst_index, burst_size)
        pos, d = ((f * 2.0 - 1.0) * radius, 0.0, 0.0), (0.0, 1.0, 0.0)
    elif kind == SHAPE['Donut']:
        a = arc * _arc_fraction(rng, shape['arc'], state, 'arc', burst_index, burst_size)
        ring = (math.cos(a) * radius, math.sin(a) * radius, 0.0)
        tube = rng.range(0.0, 2.0 * math.pi)
        rr = float(shape.get('donutRadius', 0.2)) * radial(2)
        outward = (math.cos(a), math.sin(a), 0.0)
        off = v_add(v_mul(outward, math.cos(tube) * rr), (0.0, 0.0, math.sin(tube) * rr))
        pos, d = v_add(ring, off), v_norm(off, outward)
    elif kind == SHAPE['Mesh']:
        if mesh is None:
            raise ValueError('a mesh shape needs its mesh')
        pos, d, colour = _mesh_point(shape, mesh, rng, state, burst_index, burst_size)
        if colour is not None and colour_out is not None and shape.get('m_UseMeshColors', True):
            colour_out.append(colour)
    rd = float(shape.get('randomDirectionAmount', 0.0))
    if rd > 0:
        d = v_norm(v_lerp(d, rng.unit_vector(), rd))
    sd = float(shape.get('sphericalDirectionAmount', 0.0))
    if sd > 0:
        d = v_norm(v_lerp(d, v_norm(pos, d), sd))
    rp = float(shape.get('randomPositionAmount', 0.0))
    if rp > 0:
        pos = v_add(pos, v_mul(rng.unit_vector(), rp * rng.value()))
    # The shape's own TRS (m_Position, m_Rotation in degrees, m_Scale; a box's size is its scale)
    q = q_euler_deg(*_vec(shape.get('m_Rotation')))
    scale = _vec(shape.get('m_Scale'), (1.0, 1.0, 1.0))
    pos = v_add(q_rotate(q, v_hadamard(pos, scale)), _vec(shape.get('m_Position')))
    if kind not in (SHAPE['Box'], SHAPE['BoxShell'], SHAPE['BoxEdge'], SHAPE['Rectangle']):
        d = v_hadamard(d, scale)
    d = v_norm(q_rotate(q, d), d)
    return pos, d


def mesh_shape_data(vertices: list, normals: list, triangles: list, colors: list | None) -> dict:
    """A mesh shape's mesh as the simulation samples it: vertices and normals (x, y, z), triangles (flat
    indices), colours (r, g, b, a) or None, every number float32, and `cdf`, each triangle's cumulative share of
    the area as layerParticles.json's areaCdf has it (float32, the last exactly 1)."""
    v = [tuple(f32(c) for c in p[:3]) for p in vertices]
    n = [tuple(f32(c) for c in p[:3]) for p in normals]
    col = [tuple(f32(c) for c in p[:4]) for p in colors] if colors else None
    areas = []
    for i in range(0, len(triangles), 3):
        a, b, c = v[triangles[i]], v[triangles[i + 1]], v[triangles[i + 2]]
        u, w = v_sub(b, a), v_sub(c, a)
        areas.append(0.5 * v_len(v_cross(u, w)))
    total, running, cdf = sum(areas), 0.0, []
    for area in areas:
        running += area
        cdf.append(f32(running / total))
    cdf[-1] = 1.0
    return {'vertices': v, 'normals': n, 'triangles': list(triangles), 'colors': col, 'cdf': cdf}


def _mesh_point(shape: dict, mesh: dict, rng, state: dict, burst_index, burst_size):
    """(position, normal, colour or None) on a mesh shape (section 5.2), by `placementMode`: 0 a vertex, 1 a
    point on an edge of a triangle, 2 a point in a triangle (barycentric, uniform). Random spawn picks a
    vertex uniformly and a triangle by area (binary search of `cdf`); Loop, PingPong and BurstSpread
    (`m_MeshSpawn`) step through vertices or triangles in order. Then `m_MeshNormalOffset` along the normal.
    The choices beyond Unity's documentation (edges per triangle by area, normals interpolated) are inferred."""
    placement = int(shape.get('placementMode', 0))
    spawn = shape.get('m_MeshSpawn') or {}
    random_spawn = spawn.get('mode', MULTI_RANDOM) == MULTI_RANDOM
    v, nrm, tri, col = mesh['vertices'], mesh['normals'], mesh['triangles'], mesh['colors']
    offset = float(shape.get('m_MeshNormalOffset', 0.0))
    if placement == 0:
        if random_spawn:
            i = min(int(rng.value() * len(v)), len(v) - 1)
        else:
            i = min(int(_arc_fraction(rng, spawn, state, 'mesh', burst_index, burst_size) * len(v)), len(v) - 1)
        normal = v_norm(nrm[i])
        return v_add(v[i], v_mul(normal, offset)), normal, (col[i] if col else None)
    count = len(tri) // 3
    if random_spawn:
        u = rng.value()
        lo, hi = 0, count - 1
        while lo < hi:  # the first triangle whose cumulative share reaches u
            mid = (lo + hi) // 2
            if mesh['cdf'][mid] < u:
                lo = mid + 1
            else:
                hi = mid
        t = lo
    else:
        t = min(int(_arc_fraction(rng, spawn, state, 'mesh', burst_index, burst_size) * count), count - 1)
    ia, ib, ic = tri[3 * t], tri[3 * t + 1], tri[3 * t + 2]
    if placement == 1:
        edge = min(int(rng.value() * 3), 2)
        i0, i1 = ((ia, ib), (ib, ic), (ic, ia))[edge]
        f = rng.value()
        weights = {i0: 1.0 - f, i1: f}
    else:
        r1, r2 = math.sqrt(rng.value()), rng.value()
        weights = {ia: 1.0 - r1, ib: r1 * (1.0 - r2)}
        weights[ic] = weights.get(ic, 0.0) + r1 * r2
    pos, normal = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    colour = (0.0, 0.0, 0.0, 0.0) if col else None
    for i, w in weights.items():
        pos = v_add(pos, v_mul(v[i], w))
        normal = v_add(normal, v_mul(nrm[i], w))
        if col:
            colour = tuple(c + x * w for c, x in zip(colour, col[i]))
    normal = v_norm(normal)
    return v_add(pos, v_mul(normal, offset)), normal, colour


# ---------------------------------------------------------------------------------------------------------
# Noise (section 7.6): an approximation. Unity's curl-of-Perlin field is not public; this is a smooth,
# divergence-free field with the same knobs (strength, frequency, scroll, octaves, damping).


def _hash3(ix, iy, iz):
    h = (ix * 374761393 + iy * 668265263 + iz * 2147483647) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFF) / 65535.0 * 2.0 - 1.0


def _value_noise(x, y, z):
    ix, iy, iz = math.floor(x), math.floor(y), math.floor(z)
    fx, fy, fz = x - ix, y - iy, z - iz
    sx, sy, sz = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy), fz * fz * (3 - 2 * fz)
    out = 0.0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                w = (sx if dx else 1 - sx) * (sy if dy else 1 - sy) * (sz if dz else 1 - sz)
                out += w * _hash3(ix + dx, iy + dy, iz + dz)
    return out


def curl_noise(p, octaves=1, octave_multiplier=0.5, octave_scale=2.0):
    """Curl of three offset scalar fields, central differences; roughly unit amplitude."""
    e = 1e-3
    out = (0.0, 0.0, 0.0)
    amp, freq = 1.0, 1.0
    for _ in range(max(1, octaves)):
        q = v_mul(p, freq)

        def f(k, x, y, z):
            return _value_noise(x + 31.7 * k, y + 17.3 * k, z + 11.1 * k)

        dfz_dy = (f(2, q[0], q[1] + e, q[2]) - f(2, q[0], q[1] - e, q[2])) / (2 * e)
        dfy_dz = (f(1, q[0], q[1], q[2] + e) - f(1, q[0], q[1], q[2] - e)) / (2 * e)
        dfx_dz = (f(0, q[0], q[1], q[2] + e) - f(0, q[0], q[1], q[2] - e)) / (2 * e)
        dfz_dx = (f(2, q[0] + e, q[1], q[2]) - f(2, q[0] - e, q[1], q[2])) / (2 * e)
        dfy_dx = (f(1, q[0] + e, q[1], q[2]) - f(1, q[0] - e, q[1], q[2])) / (2 * e)
        dfx_dy = (f(0, q[0], q[1] + e, q[2]) - f(0, q[0], q[1] - e, q[2])) / (2 * e)
        c = (dfz_dy - dfy_dz, dfx_dz - dfz_dx, dfy_dx - dfx_dy)
        out = v_add(out, v_mul(c, amp * 0.25))
        amp *= octave_multiplier
        freq *= octave_scale
    return out


# ---------------------------------------------------------------------------------------------------------
# Particles and the simulation (sections 4 and 7)


@dataclass
class Particle:
    position: tuple
    velocity: tuple            # persistent: start velocity + gravity + force + limit (Unity's m_Velocity)
    animated: tuple            # recomputed each step: velocity over lifetime, inherit, noise (m_AnimatedVelocity)
    start_lifetime: float
    age: float
    start_size: tuple
    start_color: tuple
    rotation: tuple            # radians (x, y, z); a 2D particle uses z
    flip: float                # +1 or -1 (flipRotation)
    rand: dict                 # per-particle constants: one lerp factor per module/axis (section 12)
    emitter_velocity: tuple = (0.0, 0.0, 0.0)
    row: int = 0
    noise_sum: tuple = (0.0, 0.0, 0.0)
    seed: int = 0              # its 32-bit seed (hashed randoms): every random value of it, and of its sub-emissions
    serial: int = 0            # its birth number in the system (orders sub-emitter births)
    subs: list | None = None   # per sub-emitter link: [generator, rate accumulator] while that link emits from it, else None
    previous: tuple | None = None  # its position at the start of the step (sub-emitter births are placed along the step)
    parent: int = -1           # a sub-emitter's particle: the serial of the parent particle it was born from

    @property
    def age01(self) -> float:
        return min(max(self.age / self.start_lifetime, 0.0), 1.0) if self.start_lifetime > 0 else 1.0


RAND_KEYS = ('size', 'sizeY', 'sizeZ', 'rotX', 'rotY', 'rotZ', 'color', 'velX', 'velY', 'velZ', 'orbX', 'orbY', 'orbZ',
             'offX', 'offY', 'offZ', 'radial', 'speedMod', 'limit', 'limitX', 'limitY', 'limitZ', 'forceX', 'forceY',
             'forceZ', 'gravity', 'noise', 'noiseY', 'noiseZ', 'noisePos', 'frame', 'startFrame', 'row', 'custom0_0',
             'custom0_1', 'custom0_2', 'custom0_3', 'custom1_0', 'custom1_1', 'custom1_2', 'custom1_3', 'customColor0',
             'customColor1', 'inherit', 'noiseOffset', 'trail')
START_KEYS = ('startSpeed', 'startLifetime', 'startSizeX', 'startSizeY', 'startSizeZ', 'startRotationX', 'startRotationY',
              'startRotationZ', 'flip', 'startColor')
SHAPE_DRAWS = 16  # keyed values one particle's shape sampling may draw, in order: shape0 ... shape15
MAX_SUB_LINKS = 8  # sub-emitter links a parent may have (the export leaves out a parent with more)
# Every per-particle random value, by key: its index here is the `key` of particle_random (the site keeps the
# same table). The over-lifetime factors (RAND_KEYS), the start values, the shape's draws, then for each
# sub-emitter link i of a parent: `sub<i>`, the roll against the link's probability, and `subSeed<i>`, the
# seed (particle_word) of the generator that link's emission from this particle draws from.
RANDOM_KEYS = (RAND_KEYS + START_KEYS + tuple(f'shape{i}' for i in range(SHAPE_DRAWS)) + tuple(f'sub{i}' for i in range(MAX_SUB_LINKS))
               + tuple(f'subSeed{i}' for i in range(MAX_SUB_LINKS)))
RANDOM_KEY = {name: index for index, name in enumerate(RANDOM_KEYS)}


class Simulation:
    """One ParticleSystem. step() follows the order in section 7.1."""

    def __init__(self, config: Config, seed: int = 1, options: Options | None = None):
        """`seed`: the seed of this play when the system draws a new one every play (autoRandomSeed; the
        site's is play_seed()); a fixed-seed system uses its randomSeed. The config is copied: set_fields
        changes this simulation's own."""
        self.c = copy.deepcopy(config)
        self.options = options or Options()
        self.links = []        # [(child Simulation, 'birth' | 'death', probability)] (section 9)
        self.is_child = False  # a sub-emitter: it emits only where its parents' particles tell it to
        if self.options.randoms == 'hashed':
            self.seed = (seed if config.auto_random_seed else config.random_seed) & M32
        else:
            self.seed = seed if config.auto_random_seed else (config.random_seed or seed)
        self._parsed = {}
        self.reset()

    def mm(self, tree: dict) -> MinMaxCurve:
        """MinMaxCurve.from_tree, parsed once per serialized dict."""
        key = id(tree)
        hit = self._parsed.get(key)
        if hit is None:
            hit = self._parsed[key] = (tree, MinMaxCurve.from_tree(tree))
        return hit[1]

    def mg(self, tree: dict) -> MinMaxGradient:
        key = ('g', id(tree))
        hit = self._parsed.get(key)
        if hit is None:
            hit = self._parsed[key] = (tree, MinMaxGradient.from_tree(tree))
        return hit[1]

    @classmethod
    def from_trees(cls, ps: dict, renderer: dict | None = None, seed: int = 1, options: Options | None = None) -> 'Simulation':
        return cls(Config.from_trees(ps, renderer), seed, options)

    @classmethod
    def from_export(cls, system: dict, document: dict, seed: int = 1, options: Options | None = None) -> 'Simulation':
        """A system of layerParticles.json (Config.from_export); its static emitter is emitter_from_export()."""
        return cls(Config.from_export(system, document), seed, options)

    # --- lifecycle

    def reset(self):
        self.rng = XorShift128(self.seed)
        self.particles: list[Particle] = []
        self.time = 0.0          # seconds since play, after simulationSpeed
        self.emitting = True
        self.acc_time = self.options.emit_accumulator_start
        self.acc_distance = 0.0
        self.shape_state = {}
        self.pose = Pose()
        self.prev_pose = Pose()
        self.emitter_velocity = (0.0, 0.0, 0.0)
        self.emitted_total = 0
        self.dead_total = 0
        self.burst_cycle_state = {}
        self.delay = 0.0 if (self.c.prewarm and self.c.looping) or self.is_child else self.c.start_delay.evaluate(0.0, self.rng.value())

    def link(self, child: 'Simulation', kind: str, probability: float):
        """Makes `child` a sub-emitter of this system (a layerParticles.json `sub` link): 'birth', the child
        emits from each of this system's particles while it lives, with its own rate and bursts timed by the
        particle's age; 'death', the child's bursts fire once where a particle dies. `probability`: of each
        particle's link firing. Only with hashed random values (the default)."""
        if kind not in ('birth', 'death'):
            raise ValueError(f'a {kind} sub-emitter')
        if self.options.randoms != 'hashed':
            raise ValueError('sub-emitters draw hashed random values')
        if len(self.links) >= MAX_SUB_LINKS:
            raise ValueError(f'more than {MAX_SUB_LINKS} sub-emitter links')
        if child.is_child or child.links:
            raise ValueError('a sub-emitter has one parent and no sub-emitters of its own')
        child.is_child = True
        child.reset()
        self.links.append((child, kind, f32(probability)))

    def set_fields(self, values: dict):
        """Values of an emitter timeline's field columns (the README's emitter timelines), which replace the
        serialized ones from now on: `speed` simulationSpeed; `emission.rate` and `emission.distance` the
        rate curves' scalar (a constant, a curve's multiplier, the max of two constants: ["r", min, max]'s second); `emission.enabled`
        (0 or 1); `main.startColor` the start colour's max colour (a constant, the second of two; a gradient
        reads none); `main.startSize`, `main.gravity`, `noise.strength`, `velocity.speedModifier`,
        `size.multiplier` the scalar of the start size (x), the gravity modifier, the noise strength (x), the
        speed modifier and the size over lifetime (x); `shape.radius` the shape's radius."""
        c = self.c
        for column, value in values.items():
            if column == 'speed':
                c.simulation_speed = float(value)
            elif column == 'emission.rate':
                c.rate_over_time.scalar = float(value)
            elif column == 'emission.enabled':
                c.emission_enabled = float(value) >= 0.5
            elif column == 'emission.distance':
                c.rate_over_distance.scalar = float(value)
            elif column == 'main.startColor':
                if c.start_color.state in (GRAD_COLOR, GRAD_TWO_COLORS):
                    c.start_color.max_color = tuple(float(v) for v in value)
            elif column == 'main.startSize':
                c.start_size[0].scalar = float(value)
            elif column == 'main.gravity':
                c.gravity_modifier.scalar = float(value)
            elif column == 'noise.strength':
                if 'NoiseModule' in c.modules:
                    self.mm(c.modules['NoiseModule']['strength']).scalar = float(value)
            elif column == 'velocity.speedModifier':
                if 'VelocityModule' in c.modules:
                    self.mm(c.modules['VelocityModule']['speedModifier']).scalar = float(value)
            elif column == 'size.multiplier':
                if 'SizeModule' in c.modules:
                    self.mm(c.modules['SizeModule']['curve']).scalar = float(value)
            elif column == 'shape.radius':
                if c.shape:
                    c.shape['radius'] = {**c.shape['radius'], 'value': float(value)}
            else:
                raise ValueError(f'no field column {column}')

    def play(self, pose: Pose | None = None, child_poses: list | None = None):
        """Start playing (playOnAwake = the GameObject became active). Prewarm simulates one loop first: its
        last prewarm_window (Options.prewarm 'window') or all of it ('full'), in equal steps of at most
        Options.prewarm_step, from a fresh state at the window's start. Its sub-emitter children start with
        it (`child_poses`: theirs, in link order) and are prewarmed with it; a child does not play itself."""
        self.reset()
        if pose is not None:
            self.pose = self.prev_pose = pose
        for k, (child, _, _) in enumerate(self.links):
            child.reset()
            if child_poses is not None and child_poses[k] is not None:
                child.pose = child.prev_pose = child_poses[k]
        if self.c.prewarm and self.c.looping and not self.is_child:
            duration = max(self.c.duration, 0.0)
            window = duration if self.options.prewarm == 'full' or self.c.prewarm_window is None else min(self.c.prewarm_window, duration)
            self.time = duration - window
            n = max(1, int(math.ceil(window / self.options.prewarm_step - 1e-9)))
            for _ in range(n):
                self._advance(window / n)
            self.time = 0.0  # the system clock restarts; the loop it simulated is the one that "already happened"
            self.burst_cycle_state = {}

    def step(self, dt: float, pose: Pose | None = None, child_poses: list | None = None):
        """Advance by dt seconds of game time (scaled by simulationSpeed). pose: the emitter's pose this frame;
        child_poses: its sub-emitter children's, in link order (None keeps one)."""
        self.prev_pose = self.pose
        if pose is not None:
            self.pose = pose
        if dt > 0:
            self.emitter_velocity = v_mul(v_sub(self.pose.position, self.prev_pose.position), 1.0 / dt)
        for k, (child, _, _) in enumerate(self.links):
            child.prev_pose = child.pose
            if child_poses is not None and child_poses[k] is not None:
                child.pose = child_poses[k]
            if dt > 0:
                child.emitter_velocity = v_mul(v_sub(child.pose.position, child.prev_pose.position), 1.0 / dt)
        self._advance(dt * self.c.simulation_speed, dt)

    # --- time

    def emission_time(self, t: float | None = None) -> float:
        """Seconds since emission began (after the start delay); negative while delayed."""
        return (self.time if t is None else t) - self.delay

    def loop_time(self, te: float) -> float:
        d = self.c.duration
        if d <= 0:
            return 0.0
        return te % d if self.c.looping else min(te, d)

    def system_t01(self, te: float) -> float:
        d = self.c.duration
        return 0.0 if d <= 0 else self.loop_time(te) / d

    # --- one step

    def _advance(self, dt: float, game_dt: float | None = None):
        """One step of dt seconds of this system's time (game_dt of game time: its sub-emitter children run
        at their own speed; a prewarm's steps are in this system's time)."""
        if game_dt is None:
            game_dt = dt / self.c.simulation_speed if self.c.simulation_speed > 0 else 0.0
        if dt <= 0:
            for child, _, _ in self.links:
                child._advance_driven(game_dt * child.c.simulation_speed, [])
            return
        t0, t1 = self.time, self.time + dt
        self.time = t1
        # 1. age and kill (frees slots for this step's emission; a death sub-emitter fires where it died)
        alive, deaths = [], []
        for p in self.particles:
            age = p.age
            p.age += dt
            if p.age < p.start_lifetime:
                alive.append(p)
            else:
                self.dead_total += 1
                if self.links:
                    deaths.append((p, min(max((p.start_lifetime - age) / dt, 0.0), 1.0)))
        self.particles = alive
        # 2. integrate the survivors over the whole step
        for p in self.particles:
            p.previous = p.position
            self._integrate(p, dt)
        # 3. emit, each new particle at its own time inside the step, aged to t1
        newborn = {}
        for te_abs, burst_index, burst_size in self._emission_events(t0, t1, dt):
            if len(self.particles) >= self.c.max_particles:
                continue  # dropped, not queued (section 4.3)
            p = self._spawn(te_abs, burst_index, burst_size, (te_abs - t0) / dt if dt else 1.0)
            p.serial, p.previous = self.emitted_total, p.position
            remaining = t1 - te_abs if self.options.subframe_emission else 0.0
            if remaining > 0:
                p.age += remaining
                self._integrate(p, remaining)
            if p.age < p.start_lifetime:
                self.particles.append(p)
                newborn[id(p)] = (te_abs - t0) / dt
            self.emitted_total += 1
        # 4. shape Loop/PingPong positions move with the arc speed curve
        if self.c.shape:
            for key, at in (('arc', 'arc'), ('radius', 'radius'), ('m_MeshSpawn', 'mesh')):
                multi = self.c.shape.get(key) or {}
                if multi.get('mode') in (MULTI_LOOP, MULTI_PINGPONG):
                    speed = self.mm(multi.get('speed')).evaluate(self.system_t01(self.emission_time(t1)))
                    self.shape_state[at] = self.shape_state.get(at, 0.0) + speed * dt
        # 5. sub-emitters: each child ages and moves its particles, then takes this step's births
        for k, (child, kind, probability) in enumerate(self.links):
            child._advance_driven(game_dt * child.c.simulation_speed, self._sub_events(k, child, kind, probability, dt, deaths, newborn))

    # --- sub-emitters (section 9)

    def _root(self, position, velocity) -> tuple:
        """A particle's position and velocity in root space (the prefab root, where children are placed)."""
        if self.c.space == SPACE_WORLD:
            return position, velocity
        return self.pose.point(position), self.pose.vector(velocity)

    def _sub_events(self, k: int, child: 'Simulation', kind: str, probability: float, dt: float, deaths: list, newborn: dict) -> list:
        """The births this step's particles give link k's child: [(fraction of the step, parent serial, n, root
        position, parent velocity in root space, child system time 0-1, particle seed, burst index, burst
        size)], sorted. Birth: from every live particle whose link fired, over the age it lived this step, the
        child's rate (its own accumulator, rate over distance by the particle's own movement) and bursts timed
        by that age, looping with the child's duration; Death: the child's bursts, once each. The draws come
        from the link's generator of that particle (seeded by its subSeed<k>), so a child's particles never
        depend on other parents' particles or on the order they are handled in."""
        events = []
        if kind == 'birth':
            for p in self.particles:
                state = p.subs[k] if p.subs else None
                if state is None:
                    continue
                born = newborn.get(id(p))
                a1 = p.age
                a0 = 0.0 if born is not None else a1 - dt
                f0 = born if born is not None else 0.0
                moved = v_len(v_sub(p.position, p.previous)) if p.previous is not None else 0.0
                n = 0
                for age, s01, burst_index, burst_size in self._child_schedule(child.c, a0, a1, state, moved):
                    w = (age - a0) / (a1 - a0) if a1 > a0 else 1.0
                    where = v_lerp(p.previous, p.position, w) if p.previous is not None else p.position
                    origin, velocity = self._root(where, v_add(p.velocity, p.animated))
                    events.append((f0 + (age - a0) / dt, p.serial, n, origin, velocity, s01, state[0].next_u32(), burst_index, burst_size))
                    n += 1
        else:
            for p, fraction in deaths:
                if not (probability >= 1.0 or particle_random(p.seed, RANDOM_KEY[f'sub{k}']) < probability):
                    continue
                rng = XorShift128(particle_word(p.seed, RANDOM_KEY[f'subSeed{k}']))
                origin, velocity = self._root(p.position, v_add(p.velocity, p.animated))
                n = 0
                for b in child.c.bursts:
                    if not (b.probability >= 1.0 or rng.value() < b.probability):
                        continue
                    count = b.count.evaluate(b.time / child.c.duration if child.c.duration > 0 else 0.0, rng.value() if b.count.is_random else 1.0)
                    count = max(0, int(math.floor(count + 0.5)))
                    for i in range(count):
                        events.append((fraction, p.serial, n, origin, velocity, 0.0, rng.next_u32(), i, count))
                        n += 1
        events.sort(key=lambda e: (e[0], e[1], e[2]))
        return events

    def _child_schedule(self, c: 'Config', a0: float, a1: float, state: list, moved: float) -> list:
        """(age, child system time 0-1, burst index, burst size) of the births one parent particle gives a birth
        sub-emitter while its age runs over [a0, a1): the child's emission with the particle's age for its
        clock (no start delay; a child that does not loop stops after its duration), `state` the particle's
        [generator, accumulator] for the link."""
        rng = state[0]
        d = c.duration
        if not c.emission_enabled or d <= 0 or a1 <= a0:
            return []
        end = a1 if c.looping else min(a1, d)
        if end <= a0:
            return []
        t01 = (lambda a: (a % d) / d) if c.looping else (lambda a: min(a, d) / d)
        out = []
        rate = c.rate_over_time.evaluate(t01(a0), rng.value() if c.rate_over_time.is_random else 1.0)
        if rate > 0:
            acc0 = state[1]
            acc1 = acc0 + rate * (end - a0)
            for k in range(1, int(math.floor(acc1)) + 1):  # see _emission_events
                age = a0 + max(k - acc0, 0.0) / rate
                out.append((age, t01(age), None, 0))
            state[1] = acc1 - math.floor(acc1)
        rod = c.rate_over_distance.evaluate(t01(a0))
        if rod > 0 and moved > 0:
            acc = (state[2] if len(state) > 2 else 0.0) + moved * rod
            count = int(math.floor(acc))
            for k in range(count):
                age = a0 + (end - a0) * (k + 1) / (count + 1)
                out.append((age, t01(age), None, 0))
            if len(state) > 2:
                state[2] = acc - count
            else:
                state.append(acc - count)
        first = int(math.floor(a0 / d)) if c.looping else 0
        last = int(math.floor(end / d)) if c.looping else 0
        for loop in range(first, last + 1):
            base = loop * d
            for b in c.bursts:
                k = 0
                while not (b.cycles and k >= b.cycles):
                    bt = b.time + k * b.interval
                    if bt >= d or base + bt >= end:
                        break
                    if base + bt >= a0 and (b.probability >= 1.0 or rng.value() < b.probability):
                        count = b.count.evaluate(bt / d, rng.value() if b.count.is_random else 1.0)
                        count = max(0, int(math.floor(count + 0.5)))
                        for i in range(count):
                            out.append((base + bt, bt / d, i, count))
                    if b.interval <= 0:
                        break
                    k += 1
        out.sort(key=lambda e: e[0])
        return out

    def _advance_driven(self, dt: float, events: list):
        """A sub-emitter child's step: its particles age, die and move over dt (its own time), then the
        parents' births of the step (from _sub_events) are added, each aged to the end of the step."""
        alive = []
        for p in self.particles:
            p.age += dt
            if p.age < p.start_lifetime:
                alive.append(p)
            else:
                self.dead_total += 1
        self.particles = alive
        for p in self.particles:
            self._integrate(p, dt)
        for fraction, serial, _, origin, velocity, s01, seed, burst_index, burst_size in events:
            if len(self.particles) >= self.c.max_particles:
                continue
            p = self._spawn(0.0, burst_index, burst_size, fraction, seed=seed, origin=origin, s01=s01, emitter_velocity=velocity)
            p.parent = serial
            remaining = (1.0 - fraction) * dt if self.options.subframe_emission else 0.0
            if remaining > 0:
                p.age += remaining
                self._integrate(p, remaining)
            if p.age < p.start_lifetime:
                self.particles.append(p)
            self.emitted_total += 1
        self.time += dt

    def _emission_events(self, t0: float, t1: float, dt: float):
        """Absolute times (with burst index and size) of the particles born in the step [t0, t1).

        Windows are half-open in emission time (time since the start delay ended): [te0, te1), so a burst at
        0 fires on the first step and each instant belongs to exactly one step (section 4.2)."""
        c = self.c
        if not c.emission_enabled or not self.emitting or c.duration <= 0 or self.is_child:
            return []
        te0, te1 = max(self.emission_time(t0), 0.0), self.emission_time(t1)
        if not c.looping:
            te1 = min(te1, c.duration)
        if te1 <= te0:
            if not c.looping and self.emission_time(t1) >= c.duration:
                self.emitting = False
            return []
        events = []
        # rate over time: acc += rate * dt; every integer k >= 1 the accumulator reaches is one particle, born at
        # the instant it reaches k (the rate is evaluated at the window start: normalized loop time). The
        # accumulator holds a fraction in [0, 1) after every step; it starts at Options.emit_accumulator_start, so a
        # start of 1 has reached 1 already: one particle at the window's start.
        rate = c.rate_over_time.evaluate(self.system_t01(te0), self.rng.value() if c.rate_over_time.is_random else 1.0)
        if rate > 0:
            acc0 = self.acc_time
            acc1 = acc0 + rate * (te1 - te0)
            for k in range(1, int(math.floor(acc1)) + 1):
                events.append((te0 + max(k - acc0, 0.0) / rate + self.delay, None, 0))
            self.acc_time = acc1 - math.floor(acc1)
        # rate over distance: the distance the emitter moved this step (world-space systems)
        rod = c.rate_over_distance.evaluate(self.system_t01(te0))
        if rod > 0:
            acc1 = self.acc_distance + v_len(v_sub(self.pose.position, self.prev_pose.position)) * rod
            n = int(math.floor(acc1))
            for k in range(n):
                events.append((t0 + dt * (k + 1) / (n + 1), None, 0))
            self.acc_distance = acc1 - n
        # bursts: in every loop, at time + k * interval for k < cycleCount (0 = until the loop ends); each cycle
        # rolls its probability; the count is evaluated at the burst's normalized time and rounded
        first_loop = int(math.floor(te0 / c.duration)) if c.looping else 0
        last_loop = int(math.floor(te1 / c.duration)) if c.looping else 0
        for loop in range(first_loop, last_loop + 1):
            base = loop * c.duration
            for b in c.bursts:
                k = 0
                while not (b.cycles and k >= b.cycles):
                    bt = b.time + k * b.interval
                    if bt >= c.duration or base + bt >= te1:
                        break
                    if base + bt >= te0 and (b.probability >= 1.0 or self.rng.value() < b.probability):
                        cnt = b.count.evaluate(bt / c.duration, self.rng.value() if b.count.is_random else 1.0)
                        cnt = max(0, int(math.floor(cnt + 0.5)))
                        for i in range(cnt):
                            events.append((base + bt + self.delay, i, cnt))
                    if b.interval <= 0:
                        break
                    k += 1
        if not c.looping and self.emission_time(t1) >= c.duration:
            self.emitting = False
        events.sort(key=lambda e: e[0])
        return events

    # --- birth

    def _spawn(self, t_abs: float, burst_index, burst_size, frame_fraction: float, *, seed: int | None = None, origin=None, s01: float | None = None,
               emitter_velocity=None) -> Particle:
        """A new particle at time t_abs. A sub-emitter's (`origin`: its parent particle's position in root
        space) comes with its seed, its system time and the parent particle's velocity."""
        c, rng = self.c, self.rng
        if s01 is None:
            s01 = self.system_t01(self.emission_time(t_abs))
        if self.options.randoms == 'hashed':
            if seed is None:
                seed = rng.next_u32()  # the one draw a birth makes from the system's generator

            def draw(key):
                return particle_random(seed, RANDOM_KEY[key])
            rand = {k: draw(k) for k in RAND_KEYS}
            shape_rng = KeyedStream(seed, RANDOM_KEY['shape0'], SHAPE_DRAWS)
        else:
            def draw(key):
                return rng.value()
            rand = {k: rng.value() for k in RAND_KEYS}  # fixed draw order -> deterministic per seed
            shape_rng = rng
        mesh_colour = []
        pos, direction = sample_shape(c.shape, shape_rng, self.shape_state, s01, burst_index, burst_size, c.shape_mesh, mesh_colour)
        speed = c.start_speed.evaluate(s01, draw('startSpeed'))
        velocity = v_mul(direction, speed)
        lifetime = c.start_lifetime.evaluate(s01, draw('startLifetime'))
        sx = c.start_size[0].evaluate(s01, draw('startSizeX'))
        if c.size3d:
            size = (sx, c.start_size[1].evaluate(s01, draw('startSizeY')), c.start_size[2].evaluate(s01, draw('startSizeZ')))
        else:
            size = (sx, sx, sx)
        if c.rotation3d:
            rot = tuple(r.evaluate(s01, draw(k)) for r, k in zip(c.start_rotation, ('startRotationX', 'startRotationY', 'startRotationZ')))
        else:
            rot = (0.0, 0.0, c.start_rotation[2].evaluate(s01, draw('startRotationZ')))
        flip = -1.0 if c.flip_rotation > 0 and draw('flip') < c.flip_rotation else 1.0
        rot = tuple(r * flip for r in rot)
        color = c.start_color.evaluate(s01, draw('startColor'))
        if mesh_colour:  # a mesh shape's vertex colour tints it (m_UseMeshColors)
            color = tuple(x * y for x, y in zip(color, mesh_colour[0]))
        color = color32(color)
        if origin is not None:  # a sub-emitter's particle: at its parent particle, the shape turned by its own emitter
            if c.space == SPACE_WORLD:
                pos, velocity = v_add(origin, self.pose.vector(pos)), self.pose.vector(velocity)
            else:
                pos = v_add(self.pose.inverse_point(origin), pos)
        elif c.space == SPACE_WORLD:
            pose = self.pose
            if self.options.subframe_emission:  # the emitter is interpolated across the frame
                pose = Pose(v_lerp(self.prev_pose.position, self.pose.position, frame_fraction), self.pose.rotation, self.pose.scale,
                            self.pose.linear)
            pos, velocity = pose.point(pos), pose.vector(velocity)
        p = Particle(position=pos, velocity=velocity, animated=(0.0, 0.0, 0.0), start_lifetime=max(lifetime, 0.0), age=0.0,
                     start_size=size, start_color=color, rotation=rot, flip=flip, rand=rand,
                     emitter_velocity=self.emitter_velocity if emitter_velocity is None else emitter_velocity,
                     seed=seed if seed is not None else 0)
        if self.links:  # which of its sub-emitter links fire from it (each with its own generator)
            p.subs = [[XorShift128(particle_word(seed, RANDOM_KEY[f'subSeed{k}'])), self.options.emit_accumulator_start]
                      if kind == 'birth' and (probability >= 1.0 or particle_random(seed, RANDOM_KEY[f'sub{k}']) < probability) else None
                      for k, (_, kind, probability) in enumerate(self.links)]
        uv = c.modules.get('UVModule')
        if uv and uv.get('animationType') == 1:
            if uv.get('rowMode', 1) == 1:
                p.row = int(rand['row'] * max(1, uv.get('tilesY', 1)) * 0.99999)
            else:
                p.row = int(uv.get('rowIndex', 0))
        return p

    # --- per-step integration (section 7.1)

    def _to_sim_space(self, v, in_world: bool):
        """A module vector given in local or world space, expressed in the simulation space."""
        if self.c.space == SPACE_WORLD and not in_world:
            return q_rotate(self.pose.rotation, v)
        if self.c.space != SPACE_WORLD and in_world:
            return q_rotate(q_conj(self.pose.rotation), v)
        return v

    def _integrate(self, p: Particle, dt: float):
        c, m, a = self.c, self.c.modules, p.age01
        # gravity: world -Y x modifier (per-particle constant for two constants), into simulation space
        g_mod = c.gravity_modifier.evaluate(self.system_t01(self.emission_time()), p.rand['gravity'])
        if g_mod:
            g = v_mul(self.options.gravity, g_mod)
            if c.space != SPACE_WORLD:
                g = self.pose.inverse_vector(g)
            p.velocity = v_add(p.velocity, v_mul(g, dt))
        force = m.get('ForceModule')
        if force:
            f = (self.mm(force['x']).evaluate(a, p.rand['forceX']),
                 self.mm(force['y']).evaluate(a, p.rand['forceY']),
                 self.mm(force['z']).evaluate(a, p.rand['forceZ']))
            p.velocity = v_add(p.velocity, v_mul(self._to_sim_space(f, bool(force.get('inWorldSpace'))), dt))
        # animated velocity (recomputed, not accumulated)
        animated = (0.0, 0.0, 0.0)
        speed_mod = 1.0
        vel = m.get('VelocityModule')
        if vel:
            lin = (self.mm(vel['x']).evaluate(a, p.rand['velX']),
                   self.mm(vel['y']).evaluate(a, p.rand['velY']),
                   self.mm(vel['z']).evaluate(a, p.rand['velZ']))
            animated = v_add(animated, self._to_sim_space(lin, bool(vel.get('inWorldSpace'))))
            orb = (self.mm(vel['orbitalX']).evaluate(a, p.rand['orbX']),
                   self.mm(vel['orbitalY']).evaluate(a, p.rand['orbY']),
                   self.mm(vel['orbitalZ']).evaluate(a, p.rand['orbZ']))
            off = (self.mm(vel['orbitalOffsetX']).evaluate(a, p.rand['offX']),
                   self.mm(vel['orbitalOffsetY']).evaluate(a, p.rand['offY']),
                   self.mm(vel['orbitalOffsetZ']).evaluate(a, p.rand['offZ']))
            radial = self.mm(vel['radial']).evaluate(a, p.rand['radial'])
            speed_mod = self.mm(vel['speedModifier']).evaluate(a, p.rand['speedMod'])
            centre = self.pose.point(off) if c.space == SPACE_WORLD else off
            rel = v_sub(p.position, centre)
            if any(orb):
                # rad/s about the system centre, turned exactly over the step the position advances by
                animated = v_add(animated, orbit_velocity(orb, rel, dt * speed_mod))
            if radial:
                animated = v_add(animated, v_mul(v_norm(rel, (0.0, 0.0, 0.0)), radial))
        inherit = m.get('InheritVelocityModule')
        if inherit and c.space == SPACE_WORLD:  # only world-space systems inherit (documented)
            k = self.mm(inherit['m_Curve']).evaluate(a, p.rand['inherit'])
            src = p.emitter_velocity if inherit.get('m_Mode', 0) == 0 else self.emitter_velocity
            animated = v_add(animated, v_mul(src, k))
        noise = m.get('NoiseModule')
        if noise and self.options.noise_enabled:
            freq = float(noise.get('frequency', 0.5))
            strength = self.mm(noise['strength']).evaluate(a, p.rand['noise'])
            if noise.get('damping'):
                strength /= max(freq, 1e-6)
            scroll = self.mm(noise['scrollSpeed']).evaluate(a) * self.time
            sample = v_add(v_mul(p.position, freq), (scroll, scroll, scroll))
            n = curl_noise(sample, int(noise.get('octaves', 1)), float(noise.get('octaveMultiplier', 0.5)),
                           float(noise.get('octaveScale', 2.0)))
            amount = self.mm(noise['positionAmount']).evaluate(a, p.rand['noisePos'])
            nv = v_mul(n, strength * amount)
            p.noise_sum = v_add(p.noise_sum, v_mul(nv, dt))
            animated = v_add(animated, nv)
        # limit velocity: dampen the speed above the limit (applied to the persistent part)
        clamp = m.get('ClampVelocityModule')
        if clamp:
            total = v_add(p.velocity, animated)
            if clamp.get('separateAxis'):
                lim = (self.mm(clamp['x']).evaluate(a, p.rand['limitX']),
                       self.mm(clamp['y']).evaluate(a, p.rand['limitY']),
                       self.mm(clamp['z']).evaluate(a, p.rand['limitZ']))
                new_total = tuple(math.copysign(self._dampen(abs(v), abs(l), float(clamp.get('dampen', 0.0)), dt), v)
                                  for v, l in zip(total, lim))
            else:
                limit = self.mm(clamp['magnitude']).evaluate(a, p.rand['limit'])
                speed = v_len(total)
                new_speed = self._dampen(speed, abs(limit), float(clamp.get('dampen', 0.0)), dt)
                new_total = v_mul(v_norm(total, (0.0, 0.0, 0.0)), new_speed)
            p.velocity = v_sub(new_total, animated)
        p.animated = animated
        p.position = v_add(p.position, v_mul(v_add(p.velocity, animated), dt * speed_mod))
        # rotation over lifetime: angular velocity (rad/s), flipped with the particle
        rot = m.get('RotationModule')
        if rot:
            w = self.mm(rot['curve']).evaluate(a, p.rand['rotZ']) * p.flip
            if rot.get('separateAxes'):
                wx = self.mm(rot['x']).evaluate(a, p.rand['rotX']) * p.flip
                wy = self.mm(rot['y']).evaluate(a, p.rand['rotY']) * p.flip
                p.rotation = (p.rotation[0] + wx * dt, p.rotation[1] + wy * dt, p.rotation[2] + w * dt)
            else:
                p.rotation = (p.rotation[0], p.rotation[1], p.rotation[2] + w * dt)

    def _dampen(self, speed: float, limit: float, d: float, dt: float) -> float:
        if speed <= limit:
            return speed
        keep = (1.0 - d) ** (dt * self.options.dampen_reference_fps) if self.options.dampen_reference_fps > 0 else (1.0 - d)
        if self.options.dampen_excess_only:
            return limit + (speed - limit) * keep
        return max(limit, speed * keep)

    # --- what a renderer needs (section 8)

    def size_of(self, p: Particle) -> tuple:
        size = p.start_size
        mod = self.c.modules.get('SizeModule')
        if mod:
            a = p.age01
            if mod.get('separateAxes'):
                k = (self.mm(mod['curve']).evaluate(a, p.rand['size']),
                     self.mm(mod['y']).evaluate(a, p.rand['sizeY']),
                     self.mm(mod['z']).evaluate(a, p.rand['sizeZ']))
            else:
                s = self.mm(mod['curve']).evaluate(a, p.rand['size'])
                k = (s, s, s)
            size = v_hadamard(size, k)
        return size

    def color_of(self, p: Particle) -> tuple:
        col = p.start_color
        mod = self.c.modules.get('ColorModule')
        if mod:
            g = self.mg(mod['gradient']).evaluate(p.age01, p.rand['color'])
            col = tuple(x * y for x, y in zip(col, g))
        return col

    def frame_of(self, p: Particle) -> tuple[int, tuple] | None:
        """(frame index, (u0, v0, u1, v1)) for a Grid texture sheet (section 7.8); frame 0 is the top-left tile."""
        uv = self.c.modules.get('UVModule')
        if not uv or uv.get('mode', 0) != 0:
            return None
        tx, ty = max(1, int(uv.get('tilesX', 1))), max(1, int(uv.get('tilesY', 1)))
        single_row = uv.get('animationType', 0) == 1
        n = tx if single_row else tx * ty
        cycles = float(uv.get('cycles', 1.0))
        fot = self.mm(uv['frameOverTime'])
        start = self.mm(uv['startFrame']).evaluate(0.0, p.rand['startFrame'])
        a = p.age01
        if self.options.texture_frame_mode == 'cocos':
            f = cycles * (fot.evaluate(a, p.rand['frame']) + start)
        else:
            x = (a * cycles) % 1.0 if a < 1.0 else 1.0
            f = fot.evaluate(x, p.rand['frame']) + start
        f = f - math.floor(f) if f >= 1.0 or f < 0 else f
        index = min(int(f * n), n - 1)
        if single_row:
            index = p.row * tx + index
        col, row = index % tx, index // tx
        u0, u1 = col / tx, (col + 1) / tx
        v1 = 1.0 - row / ty
        v0 = v1 - 1.0 / ty
        return index, (u0, v0, u1, v1)

    def custom_data_of(self, p: Particle) -> tuple[tuple, tuple]:
        """(Custom1.xyzw, Custom2.xyzw) from the Custom Data module (section 7.9)."""
        mod = self.c.modules.get('CustomDataModule')
        out = []
        for s in (0, 1):
            if not mod or mod.get(f'mode{s}', 0) == 0:
                out.append((0.0, 0.0, 0.0, 0.0))
            elif mod.get(f'mode{s}') == 1:
                n = int(mod.get(f'vectorComponentCount{s}', 4))
                out.append(tuple(self.mm(mod[f'vector{s}_{i}']).evaluate(p.age01, p.rand[f'custom{s}_{i}']) if i < n else 0.0
                                 for i in range(4)))
            else:
                out.append(self.mg(mod[f'color{s}']).evaluate(p.age01, p.rand[f'customColor{s}']))
        return out[0], out[1]

    def render_records(self) -> list[dict]:
        records = []
        for p in self.particles:
            frame = self.frame_of(p)
            c1, c2 = self.custom_data_of(p)
            records.append({'position': p.position, 'velocity': v_add(p.velocity, p.animated), 'size': self.size_of(p),
                            'rotation': p.rotation, 'color': self.color_of(p), 'age01': p.age01,
                            'frame': frame[0] if frame else None, 'uv': frame[1] if frame else (0.0, 0.0, 1.0, 1.0),
                            'custom1': c1, 'custom2': c2})
        return records


# ---------------------------------------------------------------------------------------------------------
# Rendering helpers (section 8)


def billboard_corners(center, size, rotation_z, pivot=(0.0, 0.0, 0.0), flip=(1.0, 1.0)):
    """The four corners (bottom-left, bottom-right, top-left, top-right) of a View-aligned billboard for an
    orthographic camera looking down +Z with X right and Y up: corner = centre + R(-rot) * ((corner + pivot) * size).
    A positive rotation turns the billboard clockwise on screen, and the quad moves towards its pivot, both settled
    against the recordings in P6 (particle-research verify/RECORDING-CHECK.md: X8's crossed flares turn clockwise;
    X3's flare, pivot x -0.02 of 25 units, sits 0.5 units left of its emitter)."""
    c, s = math.cos(-rotation_z), math.sin(-rotation_z)
    out = []
    for cx, cy in ((-0.5, -0.5), (0.5, -0.5), (-0.5, 0.5), (0.5, 0.5)):
        x = (cx * flip[0] + pivot[0]) * size[0]
        y = (cy * flip[1] + pivot[1]) * size[1]
        out.append((center[0] + x * c - y * s, center[1] + x * s + y * c, center[2]))
    return out


def stretched_corners(center, size, velocity, length_scale, velocity_scale, pivot=(0.0, 0.0, 0.0)):
    """Stretched billboard seen from +Z (section 8.3; corners bottom-left, bottom-right, top-left, top-right of
    the texture): the quad's long axis follows the screen-space velocity, width = size.x, length = size.y *
    lengthScale + |v| * velocityScale (a negative length flips the quad), centred on the particle. Settled against
    the recordings in P6: the texture's U runs along the stretch and its +U points against the motion (rec3's fish,
    drawn head left, swim head first), and the pivot's y moves the quad along that same axis, its x across, corner
    + pivot as billboards (X6's sparks, pivot y -0.7 with lengthScale -2, trail behind their particles from the
    chest flash). Without screen motion the axis is +Y (a stand-in: Unity keeps the last one)."""
    vx, vy = velocity[0], velocity[1]
    flat = math.hypot(vx, vy)
    dx, dy = (vx / flat, vy / flat) if flat > 1e-6 else (0.0, 1.0)
    length = size[1] * length_scale + v_len(velocity) * velocity_scale
    width = size[0]
    ex, ey = -dx, -dy  # the texture's +U and the pivot's +y: against the motion
    nx, ny = -ey, ex   # its +V: e's left, a proper turn of the quad
    out = []
    for cx, cy in ((-0.5, -0.5), (0.5, -0.5), (-0.5, 0.5), (0.5, 0.5)):
        along = (cx + pivot[1]) * length
        across = (cy + pivot[0]) * width
        out.append((center[0] + ex * along + nx * across, center[1] + ey * along + ny * across, center[2]))
    return out


def texcoord_layout(streams: list[int]) -> dict:
    """Which TEXCOORDn components each custom vertex stream lands in (RendererModuleUI packing, section 8.6):
    {'UV': ['TEXCOORD0.x', 'TEXCOORD0.y'], 'Custom1XYZW': ['TEXCOORD0.z', ...], ...}."""
    out, n, ch = {}, 0, 0
    for s in streams:
        name = VERTEX_STREAMS[s] if s < len(VERTEX_STREAMS) else str(s)
        k = STREAM_CHANNELS[s] if s < len(STREAM_CHANNELS) else 0
        if k == 0:
            out[name] = [{'Position': 'POSITION', 'Normal': 'NORMAL', 'Tangent': 'TANGENT', 'Color': 'COLOR'}.get(name, name)]
            continue
        comps = []
        for _ in range(k):
            comps.append(f'TEXCOORD{n}.{"xyzw"[ch]}')
            ch += 1
            if ch == 4:
                ch, n = 0, n + 1
        out[name] = comps
    return out


# ---------------------------------------------------------------------------------------------------------
# layerParticles.json, read back (the format in the README, written by scripts/particles.py)

# The values a field takes when the export leaves it out (it drops the ones equal to these). The site keeps
# the same table; scripts/particles.py has its own copy, and the round trip checks they agree.
EXPORT_DEFAULTS = {
    'clock': {'duration': 5, 'loop': True, 'prewarm': False, 'startDelay': 0, 'speed': 1, 'maxParticles': 1000, 'seed': None,
              'space': 'local', 'scaling': 'local'},
    'emission': {'enabled': True, 'rate': 10, 'distance': 0, 'bursts': []},
    'start': {'lifetime': 5, 'speed': 5, 'size': 1, 'rotation': 0, 'color': [1, 1, 1, 1], 'gravity': 0, 'flip': 0},
    'shape': {'radius': 1, 'thickness': 1, 'arc': 360, 'angle': 25, 'length': 5, 'donutRadius': 0.2, 'boxThickness': [0, 0, 0],
              'pos': [0, 0, 0], 'rot': [0, 0, 0], 'scale': [1, 1, 1], 'randomDir': 0, 'spherical': 0, 'randomPos': 0,
              'placement': 'vertex', 'useColors': True, 'normalOffset': 0},
    'multi': {'mode': 'random', 'spread': 0, 'speed': 1},
    'velocity': {'linear': [0, 0, 0], 'world': False, 'orbital': [0, 0, 0], 'offset': [0, 0, 0], 'radial': 0, 'speedModifier': 1},
    'limit': {'world': False},
    'force': {'world': False},
    'noise': {'frequency': 0.5, 'damping': True, 'octaves': 1, 'octaveMultiplier': 0.5, 'octaveScale': 2, 'quality': 'high', 'scroll': 0,
              'position': 1, 'rotation': 0, 'size': 0},
    'inherit': {'mode': 'initial'},
    'sheet': {'animation': 'wholeSheet', 'startFrame': 0, 'cycles': 1},
    'render': {'align': 'view', 'pivot': [0, 0, 0], 'sort': 'none', 'lengthScale': 2, 'velocityScale': 0, 'freeform': False, 'minSize': 0,
               'maxSize': 0.5, 'allowRoll': True},
}
SHAPE_TYPES = {'sphere': 0, 'sphereShell': 1, 'hemisphere': 2, 'hemisphereShell': 3, 'cone': 4, 'box': 5, 'mesh': 6, 'coneShell': 7,
               'coneVolume': 8, 'coneVolumeShell': 9, 'circle': 10, 'circleEdge': 11, 'edge': 12, 'boxShell': 15, 'boxEdge': 16, 'donut': 17,
               'rectangle': 18}
MULTI_MODES = {'random': MULTI_RANDOM, 'loop': MULTI_LOOP, 'pingPong': MULTI_PINGPONG, 'burstSpread': MULTI_BURST_SPREAD}
SPACES = {'local': SPACE_LOCAL, 'world': SPACE_WORLD}
SCALINGS = {'hierarchy': SCALING_HIERARCHY, 'local': SCALING_LOCAL, 'shape': SCALING_SHAPE}
PLACEMENTS = {'vertex': 0, 'edge': 1, 'triangle': 2}


def _get(record: dict, group: str, key: str):
    return record[key] if key in record else EXPORT_DEFAULTS[group][key]


def _mmc(value):
    return MinMaxCurve.from_export(value)


def _vec3(values):
    x, y, z = (f32(v) for v in values)
    return {'x': x, 'y': y, 'z': z}


def _multi(record: dict | None, value: float) -> dict:
    record = record or {}
    return {'value': f32(value), 'mode': MULTI_MODES[_get(record, 'multi', 'mode')], 'spread': f32(_get(record, 'multi', 'spread')),
            'speed': _mmc(_get(record, 'multi', 'speed'))}


def trees_from_export(system: dict, document: dict) -> tuple[dict, dict | None]:
    """A layerParticles.json system as the ParticleSystem and ParticleSystemRenderer typetrees it was exported
    from, as far as the simulation reads them: absent fields take EXPORT_DEFAULTS, every number is read as
    float32, curve and colour values are parsed (from_tree takes them as they are)."""
    clock, emission, start = system['clock'], system['emission'], system['start']
    seed = _get(clock, 'clock', 'seed')
    ps = {
        'lengthInSec': f32(_get(clock, 'clock', 'duration')), 'looping': _get(clock, 'clock', 'loop'), 'prewarm': _get(clock, 'clock', 'prewarm'),
        'startDelay': _mmc(_get(clock, 'clock', 'startDelay')), 'simulationSpeed': f32(_get(clock, 'clock', 'speed')),
        'moveWithTransform': SPACES[_get(clock, 'clock', 'space')], 'scalingMode': SCALINGS[_get(clock, 'clock', 'scaling')],
        'autoRandomSeed': seed is None, 'randomSeed': 0 if seed is None else int(seed),
    }
    size = _get(start, 'start', 'size')
    sizes = [_mmc(v) for v in start['sizeXYZ']] if 'sizeXYZ' in start else [_mmc(size)] * 3
    rotations = [_mmc(v) for v in start['rotationXYZ']] if 'rotationXYZ' in start else [_mmc(0), _mmc(0), _mmc(_get(start, 'start', 'rotation'))]
    ps['InitialModule'] = {
        'enabled': True, 'startLifetime': _mmc(_get(start, 'start', 'lifetime')), 'startSpeed': _mmc(_get(start, 'start', 'speed')),
        'startColor': MinMaxGradient.from_export(_get(start, 'start', 'color')), 'startSize': sizes[0], 'startSizeY': sizes[1],
        'startSizeZ': sizes[2], 'startRotationX': rotations[0], 'startRotationY': rotations[1], 'startRotation': rotations[2],
        'randomizeRotationDirection': f32(_get(start, 'start', 'flip')), 'maxNumParticles': int(_get(clock, 'clock', 'maxParticles')),
        'size3D': 'sizeXYZ' in start, 'rotation3D': 'rotationXYZ' in start, 'gravityModifier': _mmc(_get(start, 'start', 'gravity')),
    }
    ps['EmissionModule'] = {
        'enabled': _get(emission, 'emission', 'enabled'), 'rateOverTime': _mmc(_get(emission, 'emission', 'rate')),
        'rateOverDistance': _mmc(_get(emission, 'emission', 'distance')),
        'm_Bursts': [{'time': f32(b[0]), 'countCurve': _mmc(b[1]), 'cycleCount': int(b[2]), 'repeatInterval': f32(b[3]), 'probability': f32(b[4])}
                     for b in _get(emission, 'emission', 'bursts')],
    }
    shape = system.get('shape')
    if shape is None:
        ps['ShapeModule'] = {'enabled': False}
    else:
        g = lambda key: _get(shape, 'shape', key)  # noqa: E731
        ps['ShapeModule'] = {
            'enabled': True, 'type': SHAPE_TYPES[shape['type']], 'radius': _multi(shape.get('radiusMode'), g('radius')),
            'radiusThickness': f32(g('thickness')), 'arc': _multi(shape.get('arcMode'), g('arc')), 'angle': f32(g('angle')), 'length': f32(g('length')),
            'boxThickness': _vec3(g('boxThickness')), 'donutRadius': f32(g('donutRadius')), 'm_Position': _vec3(g('pos')),
            'm_Rotation': _vec3(g('rot')), 'm_Scale': _vec3(g('scale')), 'randomDirectionAmount': f32(g('randomDir')),
            'sphericalDirectionAmount': f32(g('spherical')), 'randomPositionAmount': f32(g('randomPos')),
            'placementMode': PLACEMENTS[g('placement')], 'm_MeshSpawn': _multi(shape.get('spawn'), 0.0),
            'm_UseMeshColors': g('useColors'), 'm_MeshNormalOffset': f32(g('normalOffset')), 'm_Mesh': shape.get('mesh'),
        }
    if 'size' in system:
        m = system['size']
        axes = [_mmc(v) for v in m['xyz']] if 'xyz' in m else [_mmc(m['curve'])] * 3
        ps['SizeModule'] = {'enabled': True, 'curve': axes[0], 'y': axes[1], 'z': axes[2], 'separateAxes': 'xyz' in m}
    if 'rotation' in system:
        m = system['rotation']
        axes = [_mmc(v) for v in m['xyz']] if 'xyz' in m else [_mmc(0), _mmc(0), _mmc(m['curve'])]
        ps['RotationModule'] = {'enabled': True, 'x': axes[0], 'y': axes[1], 'curve': axes[2], 'separateAxes': 'xyz' in m}
    if 'color' in system:
        ps['ColorModule'] = {'enabled': True, 'gradient': MinMaxGradient.from_export(system['color']['gradient'])}
    if 'sheet' in system:
        m = system['sheet']
        single = _get(m, 'sheet', 'animation') == 'singleRow'
        row = m.get('row', 'random')
        ps['UVModule'] = {'enabled': True, 'mode': 0, 'timeMode': 0, 'tilesX': int(m['tiles'][0]), 'tilesY': int(m['tiles'][1]),
                          'animationType': 1 if single else 0, 'rowMode': 1 if row == 'random' else 0, 'rowIndex': 0 if row == 'random' else int(row),
                          'cycles': f32(_get(m, 'sheet', 'cycles')), 'frameOverTime': _mmc(m['frameOverTime']),
                          'startFrame': _mmc(_get(m, 'sheet', 'startFrame'))}
    if 'velocity' in system:
        m = system['velocity']
        g = lambda key: _get(m, 'velocity', key)  # noqa: E731
        lin, orb, off = (_get(m, 'velocity', k) for k in ('linear', 'orbital', 'offset'))
        ps['VelocityModule'] = {'enabled': True, 'x': _mmc(lin[0]), 'y': _mmc(lin[1]), 'z': _mmc(lin[2]), 'orbitalX': _mmc(orb[0]),
                                'orbitalY': _mmc(orb[1]), 'orbitalZ': _mmc(orb[2]), 'orbitalOffsetX': _mmc(off[0]), 'orbitalOffsetY': _mmc(off[1]),
                                'orbitalOffsetZ': _mmc(off[2]), 'radial': _mmc(g('radial')), 'speedModifier': _mmc(g('speedModifier')),
                                'inWorldSpace': g('world')}
    if 'limit' in system:
        m = system['limit']
        xyz = [_mmc(v) for v in m['xyz']] if 'xyz' in m else [_mmc(0)] * 3
        ps['ClampVelocityModule'] = {'enabled': True, 'separateAxis': 'xyz' in m, 'x': xyz[0], 'y': xyz[1], 'z': xyz[2],
                                     'magnitude': _mmc(m.get('magnitude', 0)), 'dampen': f32(m['dampen']), 'inWorldSpace': _get(m, 'limit', 'world')}
    if 'force' in system:
        m = system['force']
        ps['ForceModule'] = {'enabled': True, 'x': _mmc(m['xyz'][0]), 'y': _mmc(m['xyz'][1]), 'z': _mmc(m['xyz'][2]), 'inWorldSpace': _get(m, 'force', 'world')}
    if 'noise' in system:
        m = system['noise']
        g = lambda key: _get(m, 'noise', key)  # noqa: E731
        strength = [_mmc(v) for v in m['strengthXYZ']] if 'strengthXYZ' in m else [_mmc(m['strength'])] * 3
        ps['NoiseModule'] = {'enabled': True, 'strength': strength[0], 'strengthY': strength[1], 'strengthZ': strength[2],
                             'separateAxes': 'strengthXYZ' in m, 'frequency': f32(g('frequency')), 'damping': g('damping'), 'octaves': int(g('octaves')),
                             'octaveMultiplier': f32(g('octaveMultiplier')), 'octaveScale': f32(g('octaveScale')),
                             'quality': {'low': 0, 'medium': 1, 'high': 2}[g('quality')], 'scrollSpeed': _mmc(g('scroll')),
                             'positionAmount': _mmc(g('position')), 'rotationAmount': _mmc(g('rotation')), 'sizeAmount': _mmc(g('size'))}
    if 'inherit' in system:
        m = system['inherit']
        ps['InheritVelocityModule'] = {'enabled': True, 'm_Mode': {'initial': 0, 'current': 1}[_get(m, 'inherit', 'mode')], 'm_Curve': _mmc(m['curve'])}
    if 'custom' in system:
        module = {'enabled': True}
        for slot, value in enumerate(system['custom']):
            module[f'mode{slot}'] = 0 if value is None else (1 if 'vector' in value else 2)
            vector = (value or {}).get('vector') or []
            module[f'vectorComponentCount{slot}'] = len(vector) if vector else 4
            for i in range(4):
                module[f'vector{slot}_{i}'] = _mmc(vector[i] if i < len(vector) else 0)
            module[f'color{slot}'] = MinMaxGradient.from_export((value or {}).get('color', [1, 1, 1, 1]))
        ps['CustomDataModule'] = module
    render = system.get('render')
    renderer = None if render is None else {'m_Enabled': True, 'mode': render['mode'], 'material': system.get('material')}
    return ps, renderer


def emitter_from_export(system: dict) -> tuple[Pose, tuple]:
    """(pose, the scale on particle sizes) of a system's static emitter."""
    emitter = system['emitter']
    if 'matrix' not in emitter:
        raise ValueError(f'{system["name"]}: the emitter moves (a timeline), not a static matrix')
    return Pose.from_export(emitter), tuple(f32(v) for v in emitter['scale'])


# The widths of an emitter timeline's columns (the README's emitter timelines); a material parameter's is
# its last part's (layers.json's effect paths).
COLUMN_WIDTHS = {'t': 1, 'matrix': 12, 'rotation': 4, 'scale': 3, 'active': 1, 'tint': 4, 'speed': 1, 'emission.rate': 1, 'emission.enabled': 1,
                 'emission.distance': 1, 'main.startColor': 4, 'main.startSize': 1, 'main.gravity': 1, 'noise.strength': 1,
                 'velocity.speedModifier': 1, 'shape.radius': 1, 'size.multiplier': 1, 'trail.color': 4}
PATH_WIDTHS = {'amount': 1, 'border': 1, 'main': 1, 'dissolve': 1, 'st': 4, 'offset': 4, 'intensity': 2, 'color': 4}
STEPPED_COLUMNS = ('active', 'emission.enabled')
FIELD_COLUMNS = ('speed', 'emission.rate', 'emission.enabled', 'emission.distance', 'main.startColor', 'main.startSize', 'main.gravity',
                 'noise.strength', 'velocity.speedModifier', 'shape.radius', 'size.multiplier')


def timeline_values(timeline: dict, t: float, state: str | None = None) -> dict:
    """An emitter timeline's values at time t of its clock ({column: number or list}): the frames of
    `state` (Interact...) or the default ones, interpolated linearly, stepped columns held; after `length`
    it wraps to `loopFrom` when it loops, else holds its last frame."""
    line = timeline['states'][state] if state is not None else timeline
    length, frames = f32(line['length']), line['frames']
    if t > length:
        loop_from = f32(line['loopFrom'])
        t = loop_from + (t - loop_from) % (length - loop_from) if line['loop'] and length > loop_from else length
    i = 0
    while i + 1 < len(frames) and f32(frames[i + 1][0]) <= t:
        i += 1
    a = frames[i]
    b = frames[i + 1] if i + 1 < len(frames) else a
    span = f32(b[0]) - f32(a[0])
    w = (t - f32(a[0])) / span if span > 0 else 0.0
    out, at = {}, 0
    for column in timeline['columns']:
        n = COLUMN_WIDTHS.get(column) or PATH_WIDTHS[column.rsplit('.', 1)[-1]]
        if column in STEPPED_COLUMNS:
            values = [f32(a[at])]
        else:
            values = [f32(x) + (f32(y) - f32(x)) * w for x, y in zip(a[at:at + n], b[at:at + n])]
        out[column] = values[0] if n == 1 else values
        at += n
    return out


def emitter_pose(system: dict, t: float = 0.0, state: str | None = None, placement: tuple | None = None) -> Pose:
    """The emitter's pose in root space at time t of its clock (a timeline's frame there, or the still emitter),
    for a system on a bone follower composed with `placement`, the follower's frame then: (3x4 matrix,
    row-major 12 numbers, Unity units of the root; its rotation quaternion). Hierarchy and Shape scaling take
    the matrix through the whole frame; Local only its origin, the linear part turned by the frame's rotation."""
    emitter = system['emitter']
    if 'timeline' in emitter:
        values = timeline_values(emitter['timeline'], t, state)
        m, q = values['matrix'], tuple(values['rotation'])
        q = q_mul(q, (0.0, 0.0, 0.0, 1.0))
        norm = math.sqrt(sum(x * x for x in q)) or 1.0
        q = tuple(x / norm for x in q)
    else:
        m, q = [f32(x) for x in emitter['matrix']], tuple(f32(x) for x in emitter['rotation'])
    if system.get('follow') is None:
        return Pose((m[3], m[7], m[11]), q, (1.0, 1.0, 1.0), (m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]))
    if placement is None:
        raise ValueError(f'{system["name"]}: on a bone follower; a placement is needed')
    f, fq = placement
    lin = (m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10])
    origin = v_add(_mat_vec((f[0], f[1], f[2], f[4], f[5], f[6], f[8], f[9], f[10]), (m[3], m[7], m[11])), (f[3], f[7], f[11]))
    turned = q_mul(fq, q)
    if system['clock'].get('scaling', 'local') == 'local':
        rot = [q_rotate(fq, (lin[c], lin[3 + c], lin[6 + c])) for c in range(3)]  # each column turned by the frame
    else:
        fl = (f[0], f[1], f[2], f[4], f[5], f[6], f[8], f[9], f[10])
        rot = [_mat_vec(fl, (lin[c], lin[3 + c], lin[6 + c])) for c in range(3)]
    return Pose(origin, turned, (1.0, 1.0, 1.0), (rot[0][0], rot[1][0], rot[2][0], rot[0][1], rot[1][1], rot[2][1], rot[0][2], rot[1][2], rot[2][2]))
