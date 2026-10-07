"""The illustration prefab's ParticleSystems as data: layerParticles.json and its `{"particles": [...]}` draw runs.

Each ParticleSystem the game draws becomes a record of its own Shuriken settings (clock, emission, start
values and every enabled module, with curves and gradients as data), its renderer, its material (the layer
shader description, effects.describe, plus the custom vertex inputs a particle feeds it) and where its
emitter sits, so that the site can simulate it as Unity does (scripts/tests/particle_oracle.py is the
reference simulator; its from_export reads these records back). A system is exported exactly or not at all:
everything the export cannot carry is left out with its reason in layers.json's `omitted.particleReasons`,
never drawn approximately. The format is written up in the README (layerParticles.json).

Called from layers._Exporter.run when export_layers is asked for particles (`particles=True`); nothing here
runs otherwise, and layers.json is then what it was before particles. This first export writes systems whose
emitter does not move: one on a bone follower, under an animated transform, switched or driven by a clip
(an emitter timeline) or tied to a sub-emitter is left out with a reason that says so, until those are
exported too.

Numbers: every non-integer value is a float32 (Unity serializes float32; derived values, such as the emitter
matrix and gradient key times, are rounded to one) written as the shortest decimal that reads back to the
same float32, and a reader takes them back to float32 (Math.fround). Values equal to DEFAULTS are left out.
"""
from __future__ import annotations

import json
import math
import struct

import effects
import entrance_camera as ec
import layers

FILE = 'layerParticles.json'
VERSION = 1  # layerParticles.json `version` (and the pointer's in layers.json)
# Capability tags a system may require beyond the baseline (a reader leaves out a system needing one it does
# not know, with every system tied to it): sub-emitters and trails.
REQUIRES = ('sub', 'trail')
# The groups whose textures a reader fetches in this order: always shown, then each animation's.
PHASES = ('idle', 'Start', 'Interact', 'Special')
# Reasons that mean the game draws nothing of the system either (the coverage counts leave them out).
# A system with render mode None and a Trails module draws only its trails (none are exported yet).
NOT_DRAWN = ('renderer off', 'render mode None', 'render mode None (only its trails)', 'no material', 'never active', 'emits nothing')
# A system's prewarm window when maxParticles could bind (particle_oracle.prewarm_window).
PREWARM_CAP = 30.0
# Pending: these systems are left out until emitter timelines and sub-emitter links are exported.
NOT_YET = ' (not exported yet)'

# The values a field takes when it is left out. particle_oracle.EXPORT_DEFAULTS is the reader's copy.
DEFAULTS = {
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

SHAPE_TYPES = {0: 'sphere', 1: 'sphereShell', 2: 'hemisphere', 3: 'hemisphereShell', 4: 'cone', 5: 'box', 6: 'mesh', 7: 'coneShell',
               8: 'coneVolume', 9: 'coneVolumeShell', 10: 'circle', 11: 'circleEdge', 12: 'edge', 15: 'boxShell', 16: 'boxEdge', 17: 'donut',
               18: 'rectangle'}
REFUSED_SHAPES = {13: 'a MeshRenderer', 14: 'a SkinnedMeshRenderer', 19: 'a Sprite', 20: 'a SpriteRenderer'}
# The fields each shape type reads (particle_oracle.sample_shape), besides its transform and modifiers.
SHAPE_FIELDS = {
    'sphere': ('radius', 'thickness'), 'sphereShell': ('radius',), 'hemisphere': ('radius', 'thickness'), 'hemisphereShell': ('radius',),
    'cone': ('radius', 'thickness', 'arc', 'angle'), 'coneShell': ('radius', 'arc', 'angle'),
    'coneVolume': ('radius', 'thickness', 'arc', 'angle', 'length'), 'coneVolumeShell': ('radius', 'arc', 'angle', 'length'),
    'circle': ('radius', 'thickness', 'arc'), 'circleEdge': ('radius', 'arc'), 'edge': ('radius',), 'donut': ('radius', 'thickness', 'arc', 'donutRadius'),
    'box': (), 'boxShell': ('boxThickness',), 'boxEdge': ('boxThickness',), 'rectangle': (), 'mesh': (),
}
MULTI_MODES = {0: 'random', 1: 'loop', 2: 'pingPong', 3: 'burstSpread'}
WRAPS = {0: 'pingPong', 1: 'repeat', 2: 'clamp'}
SPACES = {0: 'local', 1: 'world'}
SCALINGS = {0: 'hierarchy', 1: 'local', 2: 'shape'}
RENDER_MODES = {0: 'billboard', 1: 'stretched', 4: 'mesh'}
ALIGNMENTS = {0: 'view', 1: 'world', 2: 'local', 3: 'facing', 4: 'velocity'}
SORT_MODES = {0: 'none', 1: 'distance', 2: 'oldestInFront', 3: 'youngestInFront', 4: 'depth'}
PLACEMENTS = {0: 'vertex', 1: 'edge', 2: 'triangle'}
NOISE_QUALITY = {0: 'low', 1: 'medium', 2: 'high'}
# Modules no export carries: the game's physics, by-speed modules and the rest.
REFUSED_MODULES = {'CollisionModule': 'Collision module', 'TriggerModule': 'Triggers module', 'ExternalForcesModule': 'External forces module',
                   'LightsModule': 'Lights module', 'ColorBySpeedModule': 'Color by speed module', 'SizeBySpeedModule': 'Size by speed module',
                   'RotationBySpeedModule': 'Rotation by speed module', 'LifetimeByEmitterSpeedModule': 'Lifetime by emitter speed module'}
# Unity's ParticleSystemVertexStream values the custom inputs can name: what each puts in its TEXCOORD channels.
STREAM_CHANNELS = {4: ('uv.x', 'uv.y'), 5: ('uv2.x', 'uv2.y'), 31: ('c1.x',), 32: ('c1.x', 'c1.y'), 33: ('c1.x', 'c1.y', 'c1.z'),
                   34: ('c1.x', 'c1.y', 'c1.z', 'c1.w'), 35: ('c2.x',), 36: ('c2.x', 'c2.y'), 37: ('c2.x', 'c2.y', 'c2.z'),
                   38: ('c2.x', 'c2.y', 'c2.z', 'c2.w')}
FIXED_STREAMS = {0, 1, 2, 3}  # Position, Normal, Tangent, Color: attributes of their own
# TEXCOORD channels per stream (UnityCsReference RendererModuleUI.vertexStreamTexCoordChannels, 2021.3), to pack
# streams no input may name (they still take their channels).
CHANNEL_COUNTS = [0, 0, 0, 0, 2, 2, 2, 2, 1, 1, 3, 1, 1, 2, 3, 1, 3, 1, 3, 3, 1, 1, 1, 1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3,
                  1, 2, 3, 1]
DEFAULT_STREAMS = [0, 1, 3, 4]  # without custom streams: Position, Normal, Color, UV


class Omit(Exception):
    """A system is left out; the message is its reason."""


class NotFinite(Omit):
    """A value the export would write is not a finite float32 (the section it is in is added)."""


# ---------------------------------------------------------------------------
# Numbers and values


def num(x) -> float | int:
    """A float32 as JSON writes it: the shortest decimal that reads back to the same float32, an integer
    when it is one, never -0. Non-finite values cannot be written (Omit)."""
    x = float(x)
    if not math.isfinite(x):
        raise NotFinite(f'a value is {x}')
    target = struct.unpack('<f', struct.pack('<f', x))[0]
    if not math.isfinite(target):
        raise NotFinite(f'a value ({x}) is beyond float32')
    if target == 0:
        return 0
    value = target
    for digits in range(1, 10):
        candidate = float(f'{target:.{digits}g}')
        if struct.unpack('<f', struct.pack('<f', candidate))[0] == target:
            value = candidate
            break
    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else value


def nums(values) -> list:
    return [num(v) for v in values]


def vec3(v: dict) -> list:
    return nums((v['x'], v['y'], v['z']))


def slope(x: float):
    """A curve key's slope: null for an infinite one (a stepped key)."""
    return None if not math.isfinite(x) else num(x)


def keys_of(curve: dict) -> tuple[list, list | None]:
    """(flat [t, v, in, out, ...], [pre, post] wrap names or None when both Clamp) of an AnimationCurve."""
    out = []
    for k in curve.get('m_Curve') or []:
        if k.get('weightedMode'):
            raise Omit('a curve with weighted tangents')
        out += [num(k['time']), num(k['value']), slope(k['inSlope']), slope(k['outSlope'])]
    pre, post = curve.get('m_PreInfinity', 2), curve.get('m_PostInfinity', 2)
    if pre not in WRAPS or post not in WRAPS:
        raise Omit(f'a curve wraps with mode {pre}/{post}')
    return out, None if (pre, post) == (2, 2) else [WRAPS[pre], WRAPS[post]]


def curve(tree: dict):
    """A MinMaxCurve: a number, ["r", min, max], ["c", scalar, keys(, ["w", pre, post])] or ["cc", scalar,
    minKeys, maxKeys(, ["w", minPre, minPost, maxPre, maxPost])]. In the curve modes minScalar is not read."""
    state = tree.get('minMaxState', 0)
    if state == 0:
        return num(tree['scalar'])
    if state == 3:
        return ['r', num(tree['minScalar']), num(tree['scalar'])]
    if state == 1:
        keys, wrap = keys_of(tree['maxCurve'])
        return ['c', num(tree['scalar']), keys] + ([['w', *wrap]] if wrap else [])
    if state == 2:
        low, low_wrap = keys_of(tree['minCurve'])
        high, high_wrap = keys_of(tree['maxCurve'])
        wrap = [*(low_wrap or ['clamp', 'clamp']), *(high_wrap or ['clamp', 'clamp'])]
        return ['cc', num(tree['scalar']), low, high] + ([['w', *wrap]] if low_wrap or high_wrap else [])
    raise Omit(f'a curve in mode {state}')


def gradient(tree: dict) -> dict:
    """A Gradient: {c: [t, r, g, b, ...], a: [t, a, ...]} with times / 65535 (float32), and fixed: true for
    a stepped one."""
    nc, na = tree.get('m_NumColorKeys', 2), tree.get('m_NumAlphaKeys', 2)
    if not (1 <= nc <= 8 and 1 <= na <= 8):
        raise Omit(f'a gradient with {nc} colour and {na} alpha keys')
    c, a = [], []
    for i in range(nc):
        k = tree[f'key{i}']
        c += [num(tree[f'ctime{i}'] / 65535.0), num(k['r']), num(k['g']), num(k['b'])]
    for i in range(na):
        a += [num(tree[f'atime{i}'] / 65535.0), num(tree[f'key{i}']['a'])]
    mode = tree.get('m_Mode', 0)
    if mode not in (0, 1):
        raise Omit(f'a gradient in mode {mode}')
    return {'c': c, 'a': a, **({'fixed': True} if mode == 1 else {})}


def colour(c: dict) -> list:
    return nums((c['r'], c['g'], c['b'], c['a']))


def colour_value(tree: dict):
    """A MinMaxGradient: [r, g, b, a]; ["r", c0, c1]; ["g", G]; ["gg", G0, G1]; ["rg", G] (a random sample of G)."""
    state = tree.get('minMaxState', 0)
    if state == 0:
        return colour(tree['maxColor'])
    if state == 2:
        return ['r', colour(tree['minColor']), colour(tree['maxColor'])]
    if state == 1:
        return ['g', gradient(tree['maxGradient'])]
    if state == 3:
        return ['gg', gradient(tree['minGradient']), gradient(tree['maxGradient'])]
    if state == 4:
        return ['rg', gradient(tree['maxGradient'])]
    raise Omit(f'a colour in mode {state}')


def put(out: dict, group: str, key: str, value):
    """Writes value unless it is the default (DEFAULTS[group][key])."""
    if value != DEFAULTS[group].get(key, object()):
        out[key] = value


# ---------------------------------------------------------------------------
# Bounds (the same definitions as particle_oracle: curve_bounds, prewarm_window)


def segment_bounds(keys: list) -> tuple[float, float]:
    """(lowest, highest) value of an AnimationCurve's keys and the extrema of its segments' cubics."""
    if not keys:
        return 0.0, 0.0
    values = [k['value'] for k in keys]
    for lhs, rhs in zip(keys, keys[1:]):
        dx = rhs['time'] - lhs['time']
        if dx <= 0 or not math.isfinite(lhs['outSlope']) or not math.isfinite(rhs['inSlope']):
            continue
        p0, p1, m0, m1 = lhs['value'], rhs['value'], lhs['outSlope'] * dx, rhs['inSlope'] * dx
        a, b, c = 2 * p0 + m0 + m1 - 2 * p1, -3 * p0 - 2 * m0 - m1 + 3 * p1, m0
        roots = []
        if abs(a) > 1e-12:
            disc = b * b - 3 * a * c
            if disc >= 0:
                roots = [(-b - math.sqrt(disc)) / (3 * a), (-b + math.sqrt(disc)) / (3 * a)]
        elif abs(b) > 1e-12:
            roots = [-c / (2 * b)]
        values += [((a * s + b) * s + c) * s + p0 for s in roots if 0 < s < 1]
    return min(values), max(values)


def highest(tree: dict) -> float:
    """The highest value a MinMaxCurve takes, over any time and random factor."""
    state = tree.get('minMaxState', 0)
    if state == 0:
        return float(tree['scalar'])
    if state == 3:
        return max(float(tree['scalar']), float(tree['minScalar']))
    curves = [tree['maxCurve']] + ([tree['minCurve']] if state == 2 else [])
    bounds = [segment_bounds(c.get('m_Curve') or []) for c in curves]
    s = float(tree['scalar'])
    return max(max(lo * s, hi * s) for lo, hi in bounds)


def burst_fires(burst: dict, duration: float) -> int:
    """How many times a burst fires in one loop."""
    fires, k = 0, 0
    cycles, interval = int(burst.get('cycleCount', 1)), float(burst.get('repeatInterval', 0.01))
    while not (cycles and k >= cycles):
        if float(burst['time']) + k * interval >= duration:
            break
        fires += 1
        if interval <= 0:
            break
        k += 1
    return fires


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


# ---------------------------------------------------------------------------
# The export


class Particles:
    """The particle half of one layers export: `note` each ParticleSystem renderer as the walk meets it,
    `entries()` once the walk is done (draw entries with sort keys, for layers to sort with its own), and
    `document()` once layers has renumbered the textures."""

    def __init__(self, ex):
        self.ex = ex  # the layers._Exporter
        self.found = []  # (transform, component kind) in walk order
        self.reasons = []
        self.materials, self.material_index = [], {}
        self.meshes, self.mesh_index = [], {}  # mesh keys in first-use order, and the uses of each
        self.mesh_needs = {}
        self.material_users = {}  # material index -> set of phases of its systems
        self.sub_parents, self.sub_children = set(), set()
        self.disabled_by_script = set()

    def note(self, tr, kind: str):
        self.found.append((tr, kind))

    def omit(self, name: str, reason: str):
        self.reasons.append({'name': name, 'reason': reason})

    # --- the walk

    def entries(self) -> list:
        ex = self.ex
        self.scan()
        out = []
        seen = {}
        for tr, kind in self.found:
            # A system's name is its path in the prefab; Unity lets siblings share a name, so the second and
            # later systems on one path (in walk order) are told apart as '<path> #2', '#3'...
            path = ex.scene.path[tr]
            seen[path] = seen.get(path, 0) + 1
            name = path if seen[path] == 1 else f'{path} #{seen[path]}'
            if kind != 'ParticleSystemRenderer':
                self.omit(name, 'a MeshRenderer beside a ParticleSystem')
                continue
            try:
                record, sort = self.system(tr, name)
            except Omit as reason:
                self.omit(name, str(reason))
                continue
            except layers.LayerError as error:
                self.omit(name, str(error))
                continue
            except ec.CameraError as error:
                self.omit(name, str(error))
                continue
            except layers.UNREADABLE as error:
                self.omit(name, layers.unreadable(error))
                continue
            out.append({'particles': record, 'sort': sort})
        return out

    def scan(self):
        """Prefab-wide facts a system's own chain does not show: which systems are sub-emitters of another,
        and which GameObjects a _disableAllOnEnable script switches off."""
        ex = self.ex
        for tr in ex.order:
            for kind, _, tree in layers._component_trees(ex.scene, ex.scene.go_of[tr]):
                if kind == 'ParticleSystem' and tree and (tree.get('SubModule') or {}).get('enabled'):
                    for sub in tree['SubModule'].get('subEmitters') or []:
                        ref = (sub or {}).get('emitter') or {}
                        if not ref.get('m_PathID'):
                            continue  # an empty slot
                        self.sub_parents.add(tr)
                        if ref.get('m_FileID', 0) == 0:
                            entry = ex.read(ref['m_PathID'])
                            go = (entry[1].get('m_GameObject') or {}).get('m_PathID') if entry else None
                            if go in ex.scene.transform_of:
                                self.sub_children.add(ex.scene.transform_of[go])
                if kind == 'MonoBehaviour' and tree and '_disableAllOnEnable' in tree:
                    self.disabled_by_script |= {o.get('m_PathID') for o in tree.get('_objects') or [] if isinstance(o, dict)}

    def component(self, tr, kind: str) -> dict:
        return next((tree for k, _, tree in layers._component_trees(self.ex.scene, self.ex.scene.go_of[tr]) if k == kind), None) or {}

    def system(self, tr, name: str) -> tuple[dict, tuple]:
        """One system's record and sort key, or Omit with why it is left out."""
        ex = self.ex
        ps = self.component(tr, 'ParticleSystem')
        psr = ex.renderer_tree(tr, layers.PARTICLE)
        if not ps:
            raise Omit('no ParticleSystem beside its renderer')
        # What the game never draws either.
        if not psr.get('m_Enabled', 1):
            raise Omit('renderer off')
        trails = bool((ps.get('TrailModule') or {}).get('enabled'))
        if psr.get('m_RenderMode', 0) == 5:
            raise Omit('render mode None (only its trails)' if trails else 'render mode None')
        materials = [m for m in psr.get('m_Materials') or [] if isinstance(m, dict)]
        if not materials or not materials[0].get('m_PathID'):
            raise Omit('no material')
        toggled = ex.toggled(tr, layers.PARTICLE)
        if not ex.static_active(tr, layers.PARTICLE) and not toggled:
            raise Omit('never active')
        emission = ps['EmissionModule']
        animated = self.clip_driven(tr)
        child = tr in self.sub_children
        follower = ex.follower_on_chain(tr)
        moves = self.moves(tr)
        if not child and not animated and not self.emits(ps, moving=follower is not None or moves):
            raise Omit('emits nothing')
        # Not exported yet: emitter timelines and sub-emitter links.
        if child or tr in self.sub_parents:
            raise Omit('sub-emitter' + NOT_YET)
        if follower is not None:
            raise Omit('on a bone follower' + NOT_YET)
        if moves:
            raise Omit('under an animated transform' + NOT_YET)
        if toggled:
            raise Omit('switched by a clip' + NOT_YET)
        if animated:
            raise Omit('driven by a clip' + NOT_YET)
        # Left out for good (in this format).
        if trails:
            raise Omit('Trails module')
        for module, reason in REFUSED_MODULES.items():
            if (ps.get(module) or {}).get('enabled'):
                raise Omit(reason)
        if any(ex.scene.go_of[link] in self.disabled_by_script for link in ex.chain(tr)):
            raise Omit('listed by a _disableAllOnEnable script')
        if psr.get('m_SortingLayerID', 0) != 0:
            raise Omit(f'sorting layer {psr.get("m_SortingLayerID")}')
        only, _ = ex.group(tr)
        scroll_script, delay, map_scrolls = ex.scripts(tr)
        record = {'name': name, 'requires': [], 'only': only, 'delay': num(delay), 'active': True, 'follow': None, 'child': False}
        try:
            section = 'emitter'
            record['emitter'], z = self.emitter(tr, ps)
            section = 'main module'
            record['clock'] = self.clock(ps)
            section = 'emission'
            record['emission'] = self.emission(ps, emission)
            section = 'start values'
            record['start'] = self.start(ps['InitialModule'])
            section = 'modules'
            self.modules(ps, record)
            section = 'renderer'
            record['render'] = self.render(psr)
        except NotFinite as reason:
            raise Omit(f'{section}: {reason}') from None
        material, queue = self.material(materials[0], psr, ps, scroll_script, map_scrolls)
        record['material'] = material
        record['clock']['maxAlive'] = self.max_alive(ps)
        # Sort as layers sort (sorting layer, order, queue, distance, hierarchy), the fudge moving the system
        # back (a higher one) or forward; queues up to 2500 render in Unity's opaque pass, before every
        # transparent entry.
        fudge = float(psr.get('m_SortingFudge', 0.0))
        sort = (psr.get('m_SortingLayerID', 0), psr.get('m_SortingOrder', 0), queue, -(z + fudge), ex.walk[tr])
        if queue <= 2500:
            sort = (-math.inf,) + sort
        for mask in ex.masks:
            if mask['sort'] > sort:
                raise Omit(f'under the mask {mask["name"]} (Erase), which is not drawn')
        self.material_users.setdefault(material, set()).add(phase_of(only))
        return record, sort

    def clip_driven(self, tr) -> bool:
        """Whether a clip (default or triggered state) drives a field of the system or its renderer."""
        for scene in (self.ex.scene, *self.ex.state_scenes.values()):
            for (target, type_id, attribute) in scene.float_curves:
                if target == tr and type_id in (ec.PARTICLE_SYSTEM, ec.PARTICLE_RENDERER):
                    return True
        return False

    def moves(self, tr) -> bool:
        for scene in (self.ex.scene, *self.ex.state_scenes.values()):
            if any(scene.transform_curves.get((link, a)) for link in self.ex.chain(tr) for a in (ec.POSITION, ec.ROTATION, ec.EULER, ec.SCALE)):
                return True
        return False

    def emits(self, ps: dict, *, moving: bool) -> bool:
        """Whether the emitter puts out any particle: a rate above 0, a burst of at least one, or, for an
        emitter that moves, a rate over distance above 0."""
        em = ps['EmissionModule']
        if not em.get('enabled'):
            return False
        duration = float(ps['lengthInSec'])
        if duration <= 0:
            return False
        if highest(em['rateOverTime']) > 0 or (moving and highest(em['rateOverDistance']) > 0):
            return True
        return any(round_half_up(highest(b['countCurve'])) > 0 and burst_fires(b, duration) and float(b.get('probability', 1.0)) > 0
                   for b in em.get('m_Bursts') or [])

    # --- the emitter

    def emitter(self, tr, ps: dict) -> tuple[dict, float]:
        """The static emitter: the 3x4 matrix from the emitter's space to the prefab root (Unity units), as
        the system's scaling mode applies the transform (Local: its world position and rotation with its own
        m_LocalScale; Hierarchy and Shape: the full matrix), its rotation relative to the root, and the scale
        on particle sizes (Local: its own scale; Hierarchy: the lossy scale; Shape: none); and its depth z."""
        ex = self.ex
        scene = ex.scene
        root = scene.world(ex.root, 0.0)
        inv_root = ec.inverse(root)
        world = scene.world(tr, 0.0)
        root_q = scene.rotation(ex.root, 0.0)
        rel_q = ec.quaternion_multiply((-root_q[0], -root_q[1], -root_q[2], root_q[3]), scene.rotation(tr, 0.0))
        root_scale = [math.sqrt(sum(root[r][c] ** 2 for r in range(3))) for c in range(3)]
        scaling = int(ps.get('scalingMode', 1))
        if scaling not in SCALINGS:
            raise Omit(f'scaling mode {scaling}')
        tree = scene.tree(tr, 'Transform', 'RectTransform')
        local_scale = [float(tree['m_LocalScale'][k]) for k in 'xyz']
        if scaling == 1:
            unity = ec.trs([world[i][3] for i in range(3)], scene.rotation(tr, 0.0), local_scale)
            matrix = ec.multiply(inv_root, unity)
            size = [s / r for s, r in zip(local_scale, root_scale)]
        else:
            matrix = ec.multiply(inv_root, world)
            # Unity's lossy scale: the diagonal of R^-1 L, R the rotation and L the matrix's 3x3 part.
            r = ec.trs((0.0, 0.0, 0.0), rel_q, (1.0, 1.0, 1.0))
            size = [sum(r[k][i] * matrix[k][i] for k in range(3)) for i in range(3)] if scaling == 0 else [1.0 / v for v in root_scale]
        flat = [matrix[r][c] for r in range(3) for c in range(4)]
        return {'matrix': nums(flat), 'rotation': nums(rel_q), 'scale': nums(size)}, ex.relative(scene, tr, 0.0)[2][3]

    # --- the system's own settings

    def clock(self, ps: dict) -> dict:
        out = {}
        put(out, 'clock', 'duration', num(ps['lengthInSec']))
        put(out, 'clock', 'loop', bool(ps['looping']))
        put(out, 'clock', 'prewarm', bool(ps['prewarm']))
        window = self.prewarm_window(ps)
        if window is not None:
            out['prewarmWindow'] = num(window)
        delay = ps['startDelay']
        if delay.get('minMaxState', 0) not in (0, 3):
            raise Omit('a start delay that is a curve')
        put(out, 'clock', 'startDelay', curve(delay))
        put(out, 'clock', 'speed', num(ps.get('simulationSpeed', 1.0)))
        put(out, 'clock', 'maxParticles', int(ps['InitialModule'].get('maxNumParticles', 1000)))
        if not ps.get('autoRandomSeed', True):
            out['seed'] = int(ps.get('randomSeed', 0)) & 0xFFFFFFFF
        space = int(ps.get('moveWithTransform', 0))
        if space not in SPACES:
            raise Omit('custom simulation space')
        put(out, 'clock', 'space', SPACES[space])
        put(out, 'clock', 'scaling', SCALINGS[int(ps.get('scalingMode', 1))])
        if ps.get('ringBufferMode', 0):
            raise Omit('ring buffer mode')
        if ps.get('useUnscaledTime'):
            raise Omit('unscaled time')
        return out

    def prewarm_window(self, ps: dict) -> float | None:
        """W: the last part of the first loop a prewarm simulates (particle_oracle.prewarm_window)."""
        if not (ps['prewarm'] and ps['looping']):
            return None
        duration = max(float(ps['lengthInSec']), 0.0)
        init = ps['InitialModule']
        w = min(duration, max(0.0, highest(init['startLifetime'])))
        em = ps['EmissionModule']
        bursts = sum(max(0, round_half_up(highest(b['countCurve']))) * burst_fires(b, duration) for b in em.get('m_Bursts') or [])
        if max(0.0, highest(em['rateOverTime'])) * w + bursts > int(init.get('maxNumParticles', 1000)):
            w = min(duration, PREWARM_CAP)
        return w

    def max_alive(self, ps: dict) -> int:
        """A bound on live particles for sizing the pool (a hint: a reader grows its pool past it): the
        highest rate times the longest lifetime plus a loop's bursts at their highest, per lifetime."""
        init, em = ps['InitialModule'], ps['EmissionModule']
        duration = max(float(ps['lengthInSec']), 1e-6)
        life = max(0.0, highest(init['startLifetime']))
        bursts = sum(max(0, round_half_up(highest(b['countCurve']))) * burst_fires(b, duration) for b in em.get('m_Bursts') or [])
        loops = max(1, math.ceil(life / duration)) if ps['looping'] else 1
        bound = math.ceil(max(0.0, highest(em['rateOverTime'])) * life + bursts * loops)
        return max(1, min(int(init.get('maxNumParticles', 1000)), bound))

    def emission(self, ps: dict, em: dict) -> dict:
        out = {}
        put(out, 'emission', 'enabled', bool(em.get('enabled')))
        put(out, 'emission', 'rate', curve(em['rateOverTime']))
        put(out, 'emission', 'distance', curve(em['rateOverDistance']))
        bursts = [[num(b['time']), curve(b['countCurve']), int(b.get('cycleCount', 1)), num(b.get('repeatInterval', 0.01)), num(b.get('probability', 1.0))]
                  for b in em.get('m_Bursts') or []]
        put(out, 'emission', 'bursts', bursts)
        return out

    def start(self, init: dict) -> dict:
        out = {}
        put(out, 'start', 'lifetime', curve(init['startLifetime']))
        put(out, 'start', 'speed', curve(init['startSpeed']))
        if init.get('size3D'):
            out['sizeXYZ'] = [curve(init['startSize']), curve(init['startSizeY']), curve(init['startSizeZ'])]
        else:
            put(out, 'start', 'size', curve(init['startSize']))
        if init.get('rotation3D'):
            out['rotationXYZ'] = [curve(init['startRotationX']), curve(init['startRotationY']), curve(init['startRotation'])]
        else:
            put(out, 'start', 'rotation', curve(init['startRotation']))
        put(out, 'start', 'color', colour_value(init['startColor']))
        put(out, 'start', 'gravity', curve(init.get('gravityModifier') or {'minMaxState': 0, 'scalar': 0.0}))
        put(out, 'start', 'flip', num(init.get('randomizeRotationDirection', 0.0)))
        return out

    def modules(self, ps: dict, record: dict):
        sh = ps['ShapeModule']
        if sh.get('enabled'):
            try:
                record['shape'] = self.shape(sh)
            except NotFinite as reason:
                raise Omit(f'shape: {reason}') from None
        m = ps.get('VelocityModule') or {}
        if m.get('enabled'):
            out = {}
            put(out, 'velocity', 'linear', [curve(m['x']), curve(m['y']), curve(m['z'])])
            put(out, 'velocity', 'world', bool(m.get('inWorldSpace')))
            put(out, 'velocity', 'orbital', [curve(m['orbitalX']), curve(m['orbitalY']), curve(m['orbitalZ'])])
            put(out, 'velocity', 'offset', [curve(m['orbitalOffsetX']), curve(m['orbitalOffsetY']), curve(m['orbitalOffsetZ'])])
            put(out, 'velocity', 'radial', curve(m['radial']))
            put(out, 'velocity', 'speedModifier', curve(m['speedModifier']))
            record['velocity'] = out
        m = ps.get('ClampVelocityModule') or {}
        if m.get('enabled'):
            drag = m.get('drag') or {}
            if drag.get('minMaxState', 0) != 0 or float(drag.get('scalar', 0.0)) != 0:
                raise Omit('limit velocity drag')
            out = {}
            if m.get('separateAxis'):
                out['xyz'] = [curve(m['x']), curve(m['y']), curve(m['z'])]
                put(out, 'limit', 'world', bool(m.get('inWorldSpace')))
            else:
                out['magnitude'] = curve(m['magnitude'])
            out['dampen'] = num(m.get('dampen', 0.0))
            record['limit'] = out
        m = ps.get('ForceModule') or {}
        if m.get('enabled'):
            if m.get('randomizePerFrame'):
                raise Omit('force randomized per frame')
            out = {'xyz': [curve(m['x']), curve(m['y']), curve(m['z'])]}
            put(out, 'force', 'world', bool(m.get('inWorldSpace')))
            record['force'] = out
        m = ps.get('NoiseModule') or {}
        if m.get('enabled'):
            if m.get('remapEnabled'):
                raise Omit('noise remap')
            out = {}
            if m.get('separateAxes'):
                out['strengthXYZ'] = [curve(m['strength']), curve(m['strengthY']), curve(m['strengthZ'])]
            else:
                out['strength'] = curve(m['strength'])
            put(out, 'noise', 'frequency', num(m.get('frequency', 0.5)))
            put(out, 'noise', 'damping', bool(m.get('damping')))
            put(out, 'noise', 'octaves', int(m.get('octaves', 1)))
            put(out, 'noise', 'octaveMultiplier', num(m.get('octaveMultiplier', 0.5)))
            put(out, 'noise', 'octaveScale', num(m.get('octaveScale', 2.0)))
            quality = int(m.get('quality', 2))
            if quality not in NOISE_QUALITY:
                raise Omit(f'noise quality {quality}')
            put(out, 'noise', 'quality', NOISE_QUALITY[quality])
            put(out, 'noise', 'scroll', curve(m['scrollSpeed']))
            put(out, 'noise', 'position', curve(m['positionAmount']))
            put(out, 'noise', 'rotation', curve(m['rotationAmount']))
            put(out, 'noise', 'size', curve(m['sizeAmount']))
            record['noise'] = out
        m = ps.get('InheritVelocityModule') or {}
        if m.get('enabled'):
            out = {}
            mode = int(m.get('m_Mode', 0))
            if mode not in (0, 1):
                raise Omit(f'inherit velocity mode {mode}')
            put(out, 'inherit', 'mode', ('initial', 'current')[mode])
            out['curve'] = curve(m['m_Curve'])
            record['inherit'] = out
        m = ps.get('SizeModule') or {}
        if m.get('enabled'):
            record['size'] = {'xyz': [curve(m['curve']), curve(m['y']), curve(m['z'])]} if m.get('separateAxes') else {'curve': curve(m['curve'])}
        m = ps.get('RotationModule') or {}
        if m.get('enabled'):
            record['rotation'] = {'xyz': [curve(m['x']), curve(m['y']), curve(m['curve'])]} if m.get('separateAxes') else {'curve': curve(m['curve'])}
        m = ps.get('ColorModule') or {}
        if m.get('enabled'):
            record['color'] = {'gradient': colour_value(m['gradient'])}
        m = ps.get('UVModule') or {}
        if m.get('enabled'):
            record['sheet'] = self.sheet(m)
        m = ps.get('CustomDataModule') or {}
        if m.get('enabled'):
            slots = []
            for s in (0, 1):
                mode = int(m.get(f'mode{s}', 0))
                if mode == 0:
                    slots.append(None)
                elif mode == 1:
                    count = int(m.get(f'vectorComponentCount{s}', 4))
                    if not 1 <= count <= 4:
                        raise Omit(f'custom data with {count} components')
                    slots.append({'vector': [curve(m[f'vector{s}_{i}']) for i in range(count)]})
                elif mode == 2:
                    slots.append({'color': colour_value(m[f'color{s}'])})
                else:
                    raise Omit(f'custom data mode {mode}')
            record['custom'] = slots

    def shape(self, sh: dict) -> dict:
        kind = sh['type']
        if kind in REFUSED_SHAPES:
            raise Omit(f'shape from {REFUSED_SHAPES[kind]}')
        if kind not in SHAPE_TYPES:
            raise Omit(f'shape type {kind}')
        name = SHAPE_TYPES[kind]
        if sh.get('alignToDirection'):
            raise Omit('shape aligned to direction')
        if ((sh.get('m_Texture') or {}).get('m_PathID')):
            raise Omit('shape texture')
        out = {'type': name}
        fields = SHAPE_FIELDS[name]
        if 'radius' in fields:
            put(out, 'shape', 'radius', num(sh['radius']['value']))
            if name == 'edge':
                multi = self.multi(sh['radius'])
                if multi:
                    out['radiusMode'] = multi
        if 'thickness' in fields:
            put(out, 'shape', 'thickness', num(sh.get('radiusThickness', 1.0)))
        if 'arc' in fields:
            put(out, 'shape', 'arc', num(sh['arc']['value']))
            multi = self.multi(sh['arc'])
            if multi:
                out['arcMode'] = multi
        if 'angle' in fields:
            put(out, 'shape', 'angle', num(sh.get('angle', 25.0)))
        if 'length' in fields:
            put(out, 'shape', 'length', num(sh.get('length', 5.0)))
        if 'boxThickness' in fields:
            put(out, 'shape', 'boxThickness', vec3(sh['boxThickness']))
        if 'donutRadius' in fields:
            put(out, 'shape', 'donutRadius', num(sh.get('donutRadius', 0.2)))
        if name == 'mesh':
            ref = sh.get('m_Mesh') or {}
            if not ref.get('m_PathID'):
                raise Omit('mesh shape without a mesh')
            if sh.get('m_UseMeshMaterialIndex') and sh.get('m_MeshMaterialIndex', 0) != 0:
                raise Omit('mesh shape on a submesh')
            out['mesh'] = self.mesh(ref, shape=True)
            placement = int(sh.get('placementMode', 0))
            if placement not in PLACEMENTS:
                raise Omit(f'mesh shape placement {placement}')
            put(out, 'shape', 'placement', PLACEMENTS[placement])
            multi = self.multi(sh['m_MeshSpawn'])
            if multi:
                out['spawn'] = multi
            put(out, 'shape', 'useColors', bool(sh.get('m_UseMeshColors', True)))
            put(out, 'shape', 'normalOffset', num(sh.get('m_MeshNormalOffset', 0.0)))
        put(out, 'shape', 'pos', vec3(sh['m_Position']))
        put(out, 'shape', 'rot', vec3(sh['m_Rotation']))
        put(out, 'shape', 'scale', vec3(sh['m_Scale']))
        put(out, 'shape', 'randomDir', num(sh.get('randomDirectionAmount', 0.0)))
        put(out, 'shape', 'spherical', num(sh.get('sphericalDirectionAmount', 0.0)))
        put(out, 'shape', 'randomPos', num(sh.get('randomPositionAmount', 0.0)))
        return out

    def multi(self, value: dict) -> dict | None:
        """An arc's, edge's or mesh spawn's mode, spread and speed, or None when they are the defaults."""
        mode = int(value.get('mode', 0))
        if mode not in MULTI_MODES:
            raise Omit(f'shape spawn mode {mode}')
        out = {}
        put(out, 'multi', 'mode', MULTI_MODES[mode])
        put(out, 'multi', 'spread', num(value.get('spread', 0.0)))
        put(out, 'multi', 'speed', curve(value['speed']))
        return out or None

    def sheet(self, m: dict) -> dict:
        if m.get('mode', 0) != 0:
            raise Omit('texture sheet of sprites')
        if m.get('timeMode', 0) != 0:
            raise Omit(f'texture sheet time mode {m.get("timeMode")}')
        if m.get('uvChannelMask', -1) not in (-1, 15):
            raise Omit(f'texture sheet on UV channels {m.get("uvChannelMask")}')
        if float(m.get('flipU', 0.0)) or float(m.get('flipV', 0.0)):
            raise Omit('texture sheet flips frames')
        tiles = [int(m.get('tilesX', 1)), int(m.get('tilesY', 1))]
        if min(tiles) < 1:
            raise Omit(f'texture sheet of {tiles[0]}x{tiles[1]} tiles')
        out = {'tiles': tiles}
        single = int(m.get('animationType', 0)) == 1
        put(out, 'sheet', 'animation', 'singleRow' if single else 'wholeSheet')
        if single:
            row_mode = int(m.get('rowMode', 1))
            if row_mode == 1:
                out['row'] = 'random'
            elif row_mode == 0:
                out['row'] = int(m.get('rowIndex', 0))
            else:
                raise Omit(f'texture sheet row mode {row_mode}')
        out['frameOverTime'] = curve(m['frameOverTime'])
        put(out, 'sheet', 'startFrame', curve(m['startFrame']))
        put(out, 'sheet', 'cycles', num(m.get('cycles', 1.0)))
        return out

    # --- the renderer

    def render(self, psr: dict) -> dict:
        mode = int(psr.get('m_RenderMode', 0))
        if mode not in RENDER_MODES:
            raise Omit(f'render mode {mode}')
        out = {'mode': RENDER_MODES[mode]}
        align = int(psr.get('m_RenderAlignment', 0))
        if align not in ALIGNMENTS:
            raise Omit(f'render alignment {align}')
        put(out, 'render', 'align', ALIGNMENTS[align])
        put(out, 'render', 'pivot', vec3(psr['m_Pivot']))
        sort = int(psr.get('m_SortMode', 0))
        if sort not in SORT_MODES:
            raise Omit(f'sort mode {sort}')
        put(out, 'render', 'sort', SORT_MODES[sort])
        if mode == 1:
            put(out, 'render', 'lengthScale', num(psr.get('m_LengthScale', 2.0)))
            put(out, 'render', 'velocityScale', num(psr.get('m_VelocityScale', 0.0)))
            put(out, 'render', 'freeform', bool(psr.get('m_FreeformStretching')))
            if not psr.get('m_RotateWithStretchDirection', True):
                raise Omit('stretched without rotating to its direction')
            if float(psr.get('m_CameraVelocityScale', 0.0)):
                raise Omit('stretched by camera velocity')
        put(out, 'render', 'minSize', num(psr.get('m_MinParticleSize', 0.0)))
        put(out, 'render', 'maxSize', num(psr.get('m_MaxParticleSize', 0.5)))
        if mode == 4:
            refs = [psr.get(k) or {} for k in ('m_Mesh', 'm_Mesh1', 'm_Mesh2', 'm_Mesh3')]
            refs = [r for r in refs if r.get('m_PathID')]
            if not refs:
                raise Omit('mesh particles without a mesh')
            if len(refs) > 1 and psr.get('m_MeshDistribution', 0) != 0:
                raise Omit('mesh particles weighted between meshes')
            out['meshes'] = [self.mesh(r, shape=False) for r in refs]
        put(out, 'render', 'allowRoll', bool(psr.get('m_AllowRoll', True)))
        if any(float(psr['m_Flip'][k]) for k in 'xyz'):
            raise Omit('flipped particles')
        if psr.get('m_MaskInteraction', 0):
            raise Omit('sprite mask interaction')
        return out

    # --- meshes

    def mesh(self, ref: dict, *, shape: bool) -> int:
        """The index of a mesh (deduplicated by reference); `shape`: it emits particles, so its record also
        carries normals and the cumulative triangle areas."""
        ex = self.ex
        if ref.get('m_FileID', 0) == 0:
            key = ('bundle', ref['m_PathID'])
        else:
            cab = ex.external_of(ref)
            if cab != layers.BUILTIN_RESOURCES or ref['m_PathID'] != layers.BUILTIN_QUAD:
                raise Omit(f'mesh in {cab} ({ref["m_PathID"]})')
            key = ('quad', 0)
        if key not in self.mesh_index:
            mesh = layers.QUAD_MESH if key[0] == 'quad' else ex.mesh_of(key[1])
            self.check_mesh(mesh)
            self.mesh_index[key] = len(self.meshes)
            self.meshes.append((key, mesh))
        index = self.mesh_index[key]
        if shape:
            if key[0] == 'quad':
                raise Omit('mesh shape on the built-in quad')
            mesh = self.meshes[index][1]
            if not mesh.get('normals'):
                raise Omit('mesh shape without normals')
            if not any(area > 0 for area in triangle_areas(mesh)):
                raise Omit('mesh shape with no area')
            self.mesh_needs[index] = True
        return index

    def check_mesh(self, mesh: dict):
        if len(mesh['vertices']) > layers.MAX_VERTICES:
            raise Omit(f'mesh of {len(mesh["vertices"])} vertices')
        if len(mesh['submeshes']) != 1:
            raise Omit(f'mesh of {len(mesh["submeshes"])} submeshes')
        if not mesh['submeshes'][0] or len(mesh['submeshes'][0]) % 3:
            raise Omit('mesh without triangles')

    def mesh_records(self) -> list:
        out = []
        for index, (key, mesh) in enumerate(self.meshes):
            if key[0] == 'quad':
                out.append({'builtin': 'quad'})
                continue
            shape = self.mesh_needs.get(index, False)
            areas = triangle_areas(mesh) if shape else None
            cdf = None
            if areas:
                total, running, cdf = sum(areas), 0.0, []
                for area in areas:
                    running += area
                    cdf.append(num(running / total))
                cdf[-1] = 1
            out.append({'vertices': [num(c) for v in mesh['vertices'] for c in v[:3]], 'uvs': [num(c) for uv in mesh['uv'] for c in uv[:2]],
                        'colors': [num(c) for col in mesh['colors'] for c in col[:4]] if mesh.get('colors') else None,
                        'triangles': [int(i) for i in mesh['submeshes'][0]],
                        'normals': [num(c) for n in mesh['normals'] for c in n[:3]] if shape else None, 'areaCdf': cdf})
        return out

    # --- materials

    def material(self, ref: dict, psr: dict, ps: dict, scroll_script, map_scrolls) -> tuple[int, int]:
        """(material index, render queue) of the system's particle material, or Omit."""
        ex = self.ex
        if ref.get('m_FileID', 0) == 0:
            entry = ex.read(ref['m_PathID'])
            read, external_of, home = ex.read, ex.external_of, None
        else:
            home = ex.external_of(ref)
            source = ex.shared(home)
            if source is None or len(source) < 3:
                raise Omit(f'material in {home}')
            read, external_of = source[0], source[2]
            entry = read(ref['m_PathID'])
        if not entry or entry[0] != 'Material':
            raise Omit('material unreadable')
        material = layers.read_material(entry[1], read=read, external_of=external_of, shaders=ex.shaders, home=home)
        if material.shader is None:
            raise Omit(f'shader not found ({material.shader_ref})')
        keywords = set(material.keywords.split())
        inputs = None
        name = material.shader.name
        # Only the shaders with a custom-stream variant read the inputs; elsewhere the keyword changes nothing.
        if '_HGCUSTOMVERTEXSTREAM_ON' in keywords and name not in layers.PLAIN_TINT:
            if not psr.get('m_UseCustomVertexStreams'):
                raise Omit('custom vertex stream keyword without the renderer\'s streams')
            inputs = self.custom_inputs(psr)
        else:
            self.check_uv(psr)
        if name in layers.PLAIN_TINT:
            if 'HG_SPRITE_SHEET' in keywords:
                raise Omit('sprite sheet (HG_SPRITE_SHEET)')
            main = material.textures.get('_MainTex') or {'tex': None, 'scale': [1, 1], 'offset': [0, 0]}
            look = layers.Look(blend=layers.BLENDS.get((int(material.factor(material.shader.src)), int(material.factor(material.shader.dst))),
                                                       f'{material.factor(material.shader.src):g},{material.factor(material.shader.dst):g}'),
                               texture=main['tex'], st=[*main['scale'], *main['offset']],
                               color=list(material.colors.get('_TintColor') or [material.shader.defaults.get('_TintColor', 0.5)] * 4),
                               color_property='_TintColor', rgb_scale=1.0, alpha_scale=1.0, scroll=[0.0, 0.0],
                               cull=int(material.factor(material.shader.cull)), queue=material.queue)
            description = None
        elif name in effects.SHADERS:
            if name in effects.VERTEX:
                raise Omit('Ram/VertexDisturb on particles')
            driven = frozenset()
            if inputs is not None:
                driven = frozenset(prop for slot, source in enumerate(inputs) if self.input_driven(source, ps)
                                   for prop in effects.CUSTOM_INPUTS[name][slot])
            try:
                look = layers.effect_look(material, frozenset(), custom=driven if inputs is not None else frozenset())
            except effects.Unsupported as unsupported:
                raise Omit(str(unsupported)) from None
            description = look.effect
        else:
            raise Omit(f'shader {name}')
        if look.blend not in ('alpha', 'add'):
            raise Omit(f'blend {look.blend}')
        if look.texture is None and description is None:
            raise Omit('no main texture')
        if scroll_script and any(look.scroll):
            raise Omit('UV scroll from both its shader and a script')
        texture = ex.texture_index(look.texture) if look.texture is not None else None
        shader = ex.shader_json(description if description is not None else layers.plain_effect(look), look.st, look.scroll, scroll_script,
                                map_scrolls or {}, [1.0, 0.0, 0.0, 0.0, 1.0, 0.0])
        record = {'blend': look.blend, 'texture': texture,
                  'color': nums([2 * look.color[0] * look.rgb_scale, 2 * look.color[1] * look.rgb_scale, 2 * look.color[2] * look.rgb_scale,
                                 2 * look.color[3] * look.alpha_scale * look.opacity]),
                  'shader': shader, 'custom': None if inputs is None else {'inputs': inputs}, 'cull': look.cull}
        if record['cull'] not in (0, 1, 2):
            raise Omit(f'cull mode {record["cull"]}')
        key = json.dumps(record, sort_keys=True)
        if key not in self.material_index:
            self.material_index[key] = len(self.materials)
            self.materials.append(record)
        return self.material_index[key], look.queue

    def streams(self, psr: dict) -> list:
        streams = psr.get('m_VertexStreams') if psr.get('m_UseCustomVertexStreams') else DEFAULT_STREAMS
        if isinstance(streams, (bytes, bytearray)):
            streams = list(streams)
        return [int(s) for s in streams or []]

    def channels(self, psr: dict) -> list:
        """The TEXCOORD channels in order (TEXCOORD0.xyzw, TEXCOORD1.xyzw, ...): what each stream puts there."""
        out = []
        for s in self.streams(psr):
            if s in FIXED_STREAMS:
                continue
            if s >= len(CHANNEL_COUNTS):
                raise Omit(f'vertex stream {s}')
            out += list(STREAM_CHANNELS.get(s, (f'stream {s}',) * CHANNEL_COUNTS[s]))
        return out

    def check_uv(self, psr: dict):
        """A shader without custom streams reads its UV from TEXCOORD0.xy: the renderer's streams must put it there."""
        channels = self.channels(psr)
        if channels[:2] != ['uv.x', 'uv.y']:
            raise Omit('vertex streams move the UV')

    def custom_inputs(self, psr: dict) -> list:
        """The 8 custom inputs a _HGCUSTOMVERTEXSTREAM_ON shader reads (TEXCOORD0.zw, TEXCOORD1.xyzw,
        TEXCOORD2.xy): the source each channel holds ("uv2.x", "c1.y", ...), or 0 where no stream reaches."""
        channels = self.channels(psr)
        if channels[:2] != ['uv.x', 'uv.y']:
            raise Omit('vertex streams move the UV')
        inputs = (channels[2:10] + [0] * 8)[:8]
        for source in inputs:
            if isinstance(source, str) and source.startswith('stream '):
                raise Omit(f'custom vertex input from {source}')
        return inputs

    def input_driven(self, source, ps: dict) -> bool:
        """Whether a custom input can be other than 0 for some particle."""
        if source == 0:
            return False
        if source.startswith('uv'):
            return True
        module = ps.get('CustomDataModule') or {}
        if not module.get('enabled'):
            return False
        slot = 0 if source.startswith('c1') else 1
        component = 'xyzw'.index(source[-1])
        mode = int(module.get(f'mode{slot}', 0))
        if mode == 1:
            if component >= int(module.get(f'vectorComponentCount{slot}', 4)):
                return False
            tree = module[f'vector{slot}_{component}']
            return not (tree.get('minMaxState', 0) == 0 and float(tree['scalar']) == 0)
        if mode == 2:
            return True
        return False

    # --- the document

    def texture_refs(self, systems: list) -> list:
        """(holder dict, key) of every texture reference the systems' materials make, for layers to renumber."""
        refs = []
        for index in sorted({s['material'] for s in systems}):
            material = self.materials[index]
            refs.append((material, 'texture'))
            refs += [(m, 'texture') for m in layers.shader_maps(material['shader'])]
        return refs

    def document(self, systems: list, records: list, first: int) -> dict:
        """layerParticles.json for the systems in draw order, once layers has renumbered the textures their
        materials sample (texture_refs): `records` are the particle-only textures, numbered on from `first`."""
        ex = self.ex
        used = set()
        for system in systems:
            used.add(system['material'])
        remap = {old: new for new, old in enumerate(sorted(used))}
        materials = [self.materials[i] for i in sorted(used)]
        for system in systems:
            system['material'] = remap[system['material']]
        phases = {}
        for old in sorted(used):
            for phase in self.material_users.get(old, ()):
                for texture in material_textures(self.materials[old]):
                    phases[texture] = min(phases.get(texture, len(PHASES)), PHASES.index(phase))
        textures = [{**record, 'phase': PHASES[phases[first + i]]} for i, record in enumerate(records)]
        meshes_used = sorted({m for s in systems for m in (s['render'] or {}).get('meshes', [])}
                             | {s['shape']['mesh'] for s in systems if 'mesh' in (s.get('shape') or {})})
        mesh_records = self.mesh_records()
        mesh_remap = {old: new for new, old in enumerate(meshes_used)}
        for system in systems:
            if system['render'] and 'meshes' in system['render']:
                system['render']['meshes'] = [mesh_remap[m] for m in system['render']['meshes']]
            if 'mesh' in (system.get('shape') or {}):
                system['shape']['mesh'] = mesh_remap[system['shape']['mesh']]
        camera_size = float(ex.controller.get('_cameraSize') or 0.0)
        root = ex.scene.world(ex.root, 0.0)
        root_height = math.sqrt(sum(root[r][1] ** 2 for r in range(3)))
        return {'version': VERSION, 'unit': num(ex.unit),
                'camera': {'size': num(camera_size), 'height': num(2 * camera_size / (ex.unit * root_height))},
                'controller': {'fixFxDelay': int(ex.controller.get('_fixFxDelay') or 0), 'animTimeFixed': int(ex.controller.get('_animTimeFixed') or 0)},
                'textures': textures, 'materials': materials, 'meshes': [mesh_records[i] for i in meshes_used], 'systems': systems}


def phase_of(only: str | None) -> str:
    return 'idle' if only in (None, 'Idle') else only


def material_textures(material: dict) -> list:
    """Every texture index a particle material samples (its main texture and its shader's maps)."""
    out = [material['texture']] if material['texture'] is not None else []
    out += [m['texture'] for m in layers.shader_maps(material['shader']) if m['texture'] is not None]
    return out


def triangle_areas(mesh: dict) -> list:
    v = mesh['vertices']
    t = mesh['submeshes'][0]
    out = []
    for i in range(0, len(t), 3):
        a, b, c = v[t[i]], v[t[i + 1]], v[t[i + 2]]
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        cross = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
        out.append(0.5 * math.sqrt(sum(x * x for x in cross)))
    return out
