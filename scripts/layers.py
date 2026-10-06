"""The illustration prefab's own mesh layers: backdrops and effects the game draws with the skeleton.

An illustration prefab is more than its skeleton. Next to the SkeletonAnimation on its root it has
GameObjects with MeshRenderers (skies, windows, frames, glows), mostly under 'General Effects', and
groups the controller switches with the animation that plays ('Interact/Special/Start/Idle Only
Effects'). This module turns each of them that the site can draw exactly as the game does into a
layer: its mesh in skeleton coordinates (or in the space of the bone it follows), its texture, the
colour and blend its material gives it, where it sits in the draw order relative to the skeleton's
slot ranges (SkeletonPartsRenderer), and how its Animators move, tint and switch it over time
(Mecanim clips decoded by entrance_camera).

Never reproduced, and recorded per model in `omitted` instead: particle systems, trails, skinned
meshes, any material whose shader does more than texture x 2 x vertex colour x tint (flow
distortion, dissolve, ramps, vertex disturbance, UV rotation, ...), textures in other bundles,
animated properties or scripts whose effect is not known, and anything else that cannot be
resolved. A layer is either drawn the way the game draws it or left out with its reason.

Pure functions over typetrees (`read(path_id) -> (type name, typetree) | None`) plus injected
mesh and texture readers, so test/test_layers.py runs on synthetic scenes. scripts/sync.py
supplies UnityPy for real bundles (bundle_readers) and the shader table of the client's shared
shader bundle (shader_table).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import entrance_camera as ec

SCHEMA_VERSION = 1
FPS = 30
# The built-in meshes in Unity's "unity default resources" (by path id): only the Quad is used by
# drawable layers (the Plane appears 8 times, all left out).
BUILTIN_QUAD = 10210
BUILTIN_RESOURCES = 'unity default resources'
QUAD_MESH = {'vertices': [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (-0.5, 0.5, 0.0), (0.5, 0.5, 0.0)],
             'uv': [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], 'colors': None, 'submeshes': [[0, 3, 1, 3, 0, 2]]}
# The controller's _particles action codes: which Spine animation a group belongs to.
ACTIONS = {0: 'Idle', 1: 'Special', 2: 'Start', 3: 'Interact'}
# The Spine animations the controller's _animators are triggered with (OnIdle, OnInteract, OnSpecial).
TRIGGERS = ('Idle', 'Interact', 'Special', 'Start')
MAX_VERTICES = 65535  # 16-bit indices on the site
# Texels at or above this alpha count as showing, for the frame the site opens on (layers.json bounds).
OPAQUE_ALPHA = 8
# Decimation tolerances: positions in skeleton units, colours in 0-1 (x2 tint scale), UV.
POSITION_TOLERANCE = 0.25
COLOUR_TOLERANCE = 0.004
UV_TOLERANCE = 0.0005
# Longest loop sampled: the clips of one layer loop with their own lengths; their common period is
# sampled up to this many seconds.
MAX_PERIOD = 60.0
QUEUES = {'background': 1000, 'geometry': 2000, 'alphatest': 2450, 'transparent': 3000, 'overlay': 4000}
SPINE_QUEUE = 3000

# Shaders whose fragment maths the site reproduces (read from their GLES3 GLSL): every one computes
# texture x 2 x vertex colour x colour property, clamps alpha and blends SrcAlpha with the second
# factor its pass names. The uber shaders count only when their effect cannot change a pixel.
PLAIN_TINT = {
    'Torappu/Particles-L2D/AlphaBlend', 'Torappu/Particles/AlphaBlend', 'Torappu/Particles-L2D/Additive',
    'Torappu/Particles/Additive', 'Torappu/Particles-L2D/Multiply', 'Torappu/Particles/Multiply',
    'Torappu/Particles/AlphaBlend(ZWrite)',
}
DISTURB_CD = {'Torappu/Particles-L2D/Disturb/Disturb(CustomData)', 'Torappu/Particles/Disturb/Disturb(CustomData)'}
RAM_CD = {'Torappu/Particles-L2D/Ram/Disturb(CustomData)', 'Torappu/Particles/Ram/Disturb(CustomData)'}
ANCHOR = {'Torappu/Particles-L2D/Disturb/Disturb Anchor (AlphaBlend)', 'Torappu/Particles-L2D/Disturb/Disturb Anchor (Add)',
          'Torappu/Particles/Disturb/Disturb Anchor'}
DISSOLVE_SIMPLE = {'Torappu/Particles-L2D/Dissolve/Dissolve Add', 'Torappu/Particles-L2D/Dissolve/Dissolve AB',
                   'Torappu/Particles/Dissolve/Dissolve Add', 'Torappu/Particles/Dissolve/Dissolve AB'}
# Unity blend factors (UnityEngine.Rendering.BlendMode): SrcAlpha 5, One 1, OneMinusSrcAlpha 10.
BLENDS = {(5, 10): 'alpha', (5, 1): 'add', (0, 3): 'multiply', (1, 10): 'premultiplied', (1, 0): 'opaque', (1, 1): 'add-premultiplied'}


class LayerError(Exception):
    """One layer cannot be reproduced; the message is its reason in `omitted`."""


# ---------------------------------------------------------------------------
# Shaders


@dataclass(frozen=True)
class Shader:
    name: str
    src: float | str  # a blend factor, or the material float that holds it
    dst: float | str
    cull: float | str  # 0 off, 1 front, 2 back, or the material float
    queue: int
    defaults: dict  # property name -> default value (first component)


def _state_value(entry):
    if isinstance(entry, dict):
        name = entry.get('name')
        if isinstance(name, str) and name and name != '<noninit>':
            return name
        return entry.get('val', 0.0)
    return entry


def _tags(tags) -> dict:
    out = {}
    for item in (tags or {}).get('tags') or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out[str(item[0]).lower()] = str(item[1])
        elif isinstance(item, dict):
            out[str(item.get('first', '')).lower()] = str(item.get('second', ''))
    return out


def queue_of(text: str | None) -> int:
    """'Transparent' -> 3000, 'Transparent+1' -> 3001, 'Geometry-10' -> 1990."""
    if not text:
        return SPINE_QUEUE
    text = text.strip().lower()
    for sign in ('+', '-'):
        if sign in text:
            base, offset = text.split(sign, 1)
            try:
                return QUEUES.get(base.strip(), SPINE_QUEUE) + (int(offset) if sign == '+' else -int(offset))
            except ValueError:
                return QUEUES.get(base.strip(), SPINE_QUEUE)
    return QUEUES.get(text, SPINE_QUEUE)


def read_shader(tree: dict) -> Shader:
    """A Shader typetree (m_ParsedForm): its name, the first pass's blend and cull, queue and defaults."""
    form = tree.get('m_ParsedForm') or {}
    subshaders = form.get('m_SubShaders') or []
    if not subshaders or not subshaders[0].get('m_Passes'):
        raise LayerError(f'shader {form.get("m_Name") or tree.get("m_Name")} has no pass')
    sub = subshaders[0]
    state = sub['m_Passes'][0].get('m_State') or {}
    blend = state.get('rtBlend0') or state.get('m_RtBlend0') or {}
    queue = _tags(sub.get('m_Tags')).get('queue') or _tags(state.get('m_Tags')).get('queue')
    defaults = {}
    for prop in (form.get('m_PropInfo') or {}).get('m_Props') or []:
        defaults[prop.get('m_Name')] = prop.get('m_DefValue[0]', 0.0)
    return Shader(name=form.get('m_Name') or tree.get('m_Name') or '', src=_state_value(blend.get('srcBlend')), dst=_state_value(blend.get('destBlend')),
                  cull=_state_value(state.get('culling')), queue=queue_of(queue), defaults=defaults)


def shader_table(cab: str, objects) -> dict:
    """{(CAB name, path id): Shader} for every readable Shader among `objects` [(path id, type name, read_typetree)]."""
    table = {}
    for path_id, kind, read_tree in objects:
        if kind != 'Shader':
            continue
        try:
            table[(cab, path_id)] = read_shader(read_tree())
        except (LayerError, KeyError, TypeError, ValueError):
            continue
    return table


# ---------------------------------------------------------------------------
# Materials


def _pairs(items) -> dict:
    out = {}
    for item in items or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out[item[0]] = item[1]
        elif isinstance(item, dict):
            out[item.get('first')] = item.get('second')
    return out


@dataclass
class Material:
    name: str
    shader: Shader | None
    shader_ref: str  # what the material points at, for a reason when the shader is unknown
    keywords: str
    floats: dict
    colors: dict  # name -> [r, g, b, a]
    textures: dict  # name -> {'tex': {'id', 'w', 'h'} | {'external': cab} | None, 'scale': [x, y], 'offset': [x, y]}
    queue: int

    def float(self, name: str, default: float = 0.0) -> float:
        if name in self.floats:
            return float(self.floats[name])
        if self.shader and name in self.shader.defaults:
            return float(self.shader.defaults[name])
        return default

    def texture(self, name: str):
        return (self.textures.get(name) or {}).get('tex')

    def factor(self, value) -> float:
        """A blend or cull state: a number, or the material float it names."""
        return self.float(value) if isinstance(value, str) else float(value or 0.0)


def read_material(tree: dict, *, read, external_of, shaders: dict) -> Material:
    saved = tree.get('m_SavedProperties') or {}
    textures = {}
    for name, env in _pairs(saved.get('m_TexEnvs')).items():
        ref = (env or {}).get('m_Texture') or {}
        tex = None
        if ref.get('m_PathID'):
            if ref.get('m_FileID', 0) == 0:
                entry = read(ref['m_PathID'])
                if entry and entry[0] == 'Texture2D':
                    tex = {'id': ref['m_PathID'], 'w': entry[1].get('m_Width'), 'h': entry[1].get('m_Height')}
                else:
                    tex = {'external': 'this bundle (not a Texture2D)'}
            else:
                tex = {'external': external_of(ref)}
        textures[name] = {'tex': tex, 'scale': [env['m_Scale']['x'], env['m_Scale']['y']], 'offset': [env['m_Offset']['x'], env['m_Offset']['y']]}
    ref = tree.get('m_Shader') or {}
    shader, shader_ref = None, '(none)'
    if ref.get('m_PathID'):
        if ref.get('m_FileID', 0) == 0:
            entry = read(ref['m_PathID'])
            shader_ref = f'path id {ref["m_PathID"]} in this bundle'
            if entry and entry[0] == 'Shader':
                try:
                    shader = read_shader(entry[1])
                except LayerError:
                    shader = None
        else:
            cab = external_of(ref)
            shader_ref = f'{cab}:{ref["m_PathID"]}'
            shader = shaders.get((cab, ref['m_PathID']))
    keywords = tree.get('m_ShaderKeywords') or ' '.join(tree.get('m_ValidKeywords') or [])
    custom_queue = tree.get('m_CustomRenderQueue', -1)
    queue = custom_queue if isinstance(custom_queue, int) and custom_queue >= 0 else (shader.queue if shader else SPINE_QUEUE)
    return Material(name=tree.get('m_Name', ''), shader=shader, shader_ref=shader_ref, keywords=keywords or '',
                    floats={k: v for k, v in _pairs(saved.get('m_Floats')).items() if isinstance(v, (int, float))},
                    colors={k: [v['r'], v['g'], v['b'], v['a']] for k, v in _pairs(saved.get('m_Colors')).items() if isinstance(v, dict)},
                    textures=textures, queue=queue)


def property_name(m: Material, hash28: int) -> str:
    """The material property a curve's attribute hash names (crc32 low 28 bits), or the hash."""
    for name in [*m.floats, *m.colors, *(f'{t}_ST' for t in m.textures), *(m.shader.defaults if m.shader else {})]:
        if isinstance(name, str) and ec.crc(name) & 0x0FFFFFFF == hash28:
            return name
    return f'#{hash28:07x}'


def _round_even(x: float) -> float:
    return float(round(x))  # Python rounds half to even, as the shaders' roundEven does


def dissolve_factor(m: Material, texture: str = '_DissolveTex', amount: str = '_Amount', border: str = '_BorderWidth') -> float | None:
    """The dissolve's effect: None when it varies across the surface (a dissolve texture with
    _Amount > 0), else the constant factor it multiplies alpha by."""
    a = m.float(amount, 0.5)
    if a <= 0:
        return 1.0
    if m.texture(texture):
        return None
    bw = m.float(border, 0.1) or 1e-6
    k = 1 - _round_even(a + 0.5)
    return max(0.0, min(1.0, (1.0 - a + bw * k) / bw))


@dataclass
class Look:
    """How a plain material draws: blend, main texture and its tiling (ST), the colour property and
    its scales, the UV scroll the shader applies (UV units per second, Unity space) and its cull."""
    blend: str
    texture: dict | None
    st: list
    color: list
    color_property: str
    rgb_scale: float
    alpha_scale: float
    scroll: list
    cull: int
    queue: int


def flow_distortion(m: Material, name: str) -> dict | None:
    """The UV displacement a flow-distortion material applies (None when it applies none)."""
    main = m.texture('_MainTex') or {}
    if name in DISTURB_CD or name in RAM_CD:
        iu, iv = m.float('_IntensityU'), m.float('_IntensityV')
        influence = m.float('_DisturbScale', 1.0) if name in DISTURB_CD else m.float('_DisturbInfluenceMainUV', 1.0)
        if m.texture('_DisturbTex') and (iu or iv) and influence:
            return {'uv': round(max(abs(iu), abs(iv)) * abs(influence), 4),
                    'texels': round(max(abs(iu) * (main.get('w') or 0), abs(iv) * (main.get('h') or 0)) * abs(influence), 1)}
        return None
    if name in ANCHOR:
        for texture, iu, iv, toggle in (('_DisturTex', '_IntensityU', '_IntensityV', None),
                                        ('_DisturTex_02', '_IntensityU_02', '_IntensityV_02', '_ToggleUseDisturb2')):
            if toggle and not m.float(toggle):
                continue
            a, b = m.float(iu), m.float(iv)
            if (a or b) and (m.texture(texture) or ('_WEIGHT_ON' in m.keywords and m.texture('_WeightTex'))):
                return {'uv': round(max(abs(a), abs(b)), 4),
                        'texels': round(max(abs(a) * (main.get('w') or 0), abs(b) * (main.get('h') or 0)), 1)}
    return None


def look(m: Material) -> Look:
    """The material as the site draws it, or LayerError with why it cannot be."""
    if m.shader is None:
        raise LayerError(f'shader not found ({m.shader_ref})')
    raw = m.shader.name
    name = raw.replace('Hidden/', '', 1) if raw.startswith('Hidden/Torappu') else raw
    keywords = m.keywords
    main = m.textures.get('_MainTex') or {'tex': None, 'scale': [1, 1], 'offset': [0, 0]}
    st = [*main['scale'], *main['offset']]
    blend = BLENDS.get((int(m.factor(m.shader.src)), int(m.factor(m.shader.dst))), f'{m.factor(m.shader.src):g},{m.factor(m.shader.dst):g}')
    cull = int(m.factor(m.shader.cull))

    def plain(color_property, rgb_scale=1.0, alpha_scale=1.0, scroll=(0.0, 0.0), st_=None):
        color = list(m.colors.get(color_property) or [m.shader.defaults.get(color_property, 0.5)] * 4)
        return Look(blend=blend, texture=main['tex'], st=list(st_ or st), color=color, color_property=color_property,
                    rgb_scale=rgb_scale, alpha_scale=alpha_scale, scroll=list(scroll), cull=cull, queue=m.queue)

    if name in PLAIN_TINT:
        if 'HG_SPRITE_SHEET' in keywords:
            raise LayerError('sprite sheet (HG_SPRITE_SHEET)')
        return plain('_TintColor')
    if name in DISTURB_CD or name in RAM_CD:
        if '_HG_UV_ROTATION' in keywords:
            raise LayerError('UV rotation (_HG_UV_ROTATION)')
        if '_HGCUSTOMVERTEXSTREAM_ON' in keywords:
            raise LayerError('custom vertex stream (_HGCUSTOMVERTEXSTREAM_ON)')
        flow = flow_distortion(m, name)
        if flow:
            raise LayerError(f'flow distortion (up to {flow["uv"]:g} UV, {flow["texels"]:g} texels)')
        dissolve = dissolve_factor(m)
        if dissolve is None:
            raise LayerError(f'dissolve (amount {m.float("_Amount", 0.5):g})')
        if name in RAM_CD and m.texture('_RamTex'):
            raise LayerError('ramp texture')
        return plain('_MainColor', alpha_scale=m.float('_Opacity', 1.0) * dissolve, scroll=(m.float('_MainUSpeed'), m.float('_MainVSpeed')))
    if name in ANCHOR:
        flow = flow_distortion(m, name)
        if flow:
            raise LayerError(f'flow distortion (up to {flow["uv"]:g} UV, {flow["texels"]:g} texels)')
        # Without a distortion texture the anchor only shifts the UVs by a constant.
        offset = [0.0, 0.0]
        for iu, iv, au, av, toggle in (('_IntensityU', '_IntensityV', '_AnchorU', '_AnchorV', None),
                                       ('_IntensityU_02', '_IntensityV_02', '_AnchorU_02', '_AnchorV_02', '_ToggleUseDisturb2')):
            if toggle and not m.float(toggle):
                continue
            offset[0] -= m.float(au, 0.5) * m.float(iu)
            offset[1] -= m.float(av, 0.5) * m.float(iv)
        dissolve = 1.0
        if m.float('_ToggleUseDissolve'):
            dissolve = dissolve_factor(m)
            if dissolve is None:
                raise LayerError(f'dissolve (amount {m.float("_Amount", 0.5):g})')
        main_color = m.colors.get('_MainColor') or [0.5] * 4
        gain = m.float('_MainColorACtrl', 1.0) * (main_color[3] - 1) + 1
        tween = m.colors.get('_UVTween') or [0, 0, 0, 0]
        return plain('_MainColor', rgb_scale=gain, alpha_scale=gain * dissolve, scroll=(tween[0], tween[1]),
                     st_=[st[0], st[1], st[2] + offset[0] * st[0], st[3] + offset[1] * st[1]])
    if name in DISSOLVE_SIMPLE:
        dissolve = dissolve_factor(m)
        if dissolve is None:
            raise LayerError(f'dissolve (amount {m.float("_Amount", 0.5):g})')
        return plain('_TintColor', alpha_scale=dissolve)
    raise LayerError(f'shader {raw}')


# ---------------------------------------------------------------------------
# Animator states


def _controller_triggers(controller: dict) -> dict:
    """Trigger parameter name -> id, from the controller's string table."""
    names = {}
    for item in controller.get('m_TOS') or []:
        key, value = (item[0], item[1]) if isinstance(item, (list, tuple)) else (item.get('first'), item.get('second'))
        names[key] = value
    out = {}
    values = ((controller.get('m_Controller') or {}).get('m_Values') or {}).get('data', {}).get('m_ValueArray') or []
    for value in values:
        if value.get('m_Type') == 9 and value.get('m_ID') in names:
            out[names[value['m_ID']]] = value['m_ID']
    return out


def _state(controller: dict, index: int) -> dict:
    layer = controller['m_Controller']['m_LayerArray'][0]['data']
    machine = controller['m_Controller']['m_StateMachineArray'][layer['m_StateMachineIndex']]['data']
    return machine['m_StateConstantArray'][index]['data']


def state_clips(controller: dict, state: dict, clip_count: int) -> tuple[list[int], float]:
    ids = [node['data']['m_ClipID'] for tree in state['m_BlendTreeConstantArray'] for node in tree['data']['m_NodeArray']]
    return [i for i in ids if isinstance(i, int) and 0 <= i < clip_count], float(state.get('m_Speed', 1.0) or 1.0)


def trigger_states(controller: dict, clip_count: int) -> dict:
    """For a controller the illustration's controller triggers (_animators): Spine animation name ->
    (clip indexes, speed) of the state its trigger (On<name>) leads to from the default state or any
    state. The default state itself is under None."""
    try:
        layer = controller['m_Controller']['m_LayerArray'][0]['data']
        machine = controller['m_Controller']['m_StateMachineArray'][layer['m_StateMachineIndex']]['data']
        default = machine['m_DefaultState']
        out = {None: state_clips(controller, _state(controller, default), clip_count)}
        triggers = _controller_triggers(controller)
        transitions = list(machine.get('m_AnyStateTransitionConstantArray') or [])
        transitions += list(_state(controller, default).get('m_TransitionConstantArray') or [])
        for animation in TRIGGERS:
            event = triggers.get(f'On{animation}')
            if event is None:
                continue
            for transition in transitions:
                data = transition['data']
                if any(c['data'].get('m_EventID') == event and c['data'].get('m_ConditionMode') == 1 for c in data.get('m_ConditionConstantArray') or []):
                    out[animation] = state_clips(controller, _state(controller, data['m_DestinationState']), clip_count)
                    break
        return out
    except (KeyError, IndexError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# The export


@dataclass
class LayerExport:
    """layers.json without its bounds (scripts/sync.py adds them from the Spine runtime), the texture
    images in index order (as shipped) with how their alpha measured, and counts for the run log."""
    document: dict
    textures: list = field(default_factory=list)
    texture_info: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)


def _component_trees(scene: ec._Scene, go: int):
    return [(kind, path_id, scene.read(path_id)[1]) for kind, path_id in scene.components(go)]


def _matrix_scale(k) -> float:
    return max(math.hypot(k[0][0], k[1][0]), math.hypot(k[0][1], k[1][1]), 1e-9)


def _z_angle(q) -> float:
    """The rotation about z (degrees) of a quaternion that turns only in the plane (else LayerError)."""
    x, y, z, w = q
    if abs(x) > 1e-4 or abs(y) > 1e-4:
        # A half turn about an in-plane axis mirrors; the follower formula has no room for it.
        raise LayerError('bone follower turned out of the plane')
    return math.degrees(2 * math.atan2(z, w))


def _lcm_period(lengths: list[float]) -> float | None:
    """The common period of looping clips (seconds), or None beyond MAX_PERIOD."""
    frames = []
    for length in lengths:
        n = round(length * FPS)
        if n <= 0:
            continue
        if abs(length * FPS - n) >= 0.05:
            return None
        frames.append(n)
    period = 1
    for n in frames:
        period = period * n // math.gcd(period, n)
        if period > MAX_PERIOD * FPS:
            return None
    return period / FPS


def texture_alpha(image, classify) -> dict:
    """How a layer texture's colour measures, the way atlas pages are measured (l2d.classify_alpha), for
    the run report. Unlike Spine's pages it changes nothing: every shader a layer can have blends
    SrcAlpha / OneMinusSrcAlpha (or One), so the game samples the texture as straight alpha whatever
    the colour under its transparent texels looks like, and so does the site."""
    low, _ = image.getchannel('A').getextrema()
    if low == 255:
        return {'alpha': 'opaque', 'transparentColour': None, 'semiColourAboveAlpha': None}
    return classify(image)


class _Exporter:
    def __init__(self, root_go: int, read, *, mesh_of, texture_of, classify_texture, external_of, shaders, slots):
        self.read = read
        self.slots = set(slots)
        self.mesh_of = mesh_of
        self.texture_of = texture_of
        self.classify_texture = classify_texture
        self.external_of = external_of
        self.shaders = shaders
        self.root_go = root_go
        self.scene = ec._Scene(root_go, read, choose=self._choose_default)
        self.root = next(tr for tr, parent in self.scene.parent.items() if parent is None)
        self.order = list(self.scene.path)
        self.walk = {tr: i for i, tr in enumerate(self.order)}
        self.skeleton_component = None
        self.controller = {}
        self.parts_renderers = []
        for kind, path_id, tree in _component_trees(self.scene, root_go):
            if kind != 'MonoBehaviour' or not tree:
                continue
            if 'skeletonDataAsset' in tree and self.skeleton_component is None:
                self.skeleton_component = (path_id, tree)
            if '_particles' in tree:
                self.controller = tree
            if 'partsRenderers' in tree:
                self.parts_renderers = [r.get('m_PathID') for r in tree['partsRenderers'] if isinstance(r, dict) and r.get('m_FileID', 0) == 0]
        if self.skeleton_component is None:
            raise LayerError('the illustration prefab has no SkeletonAnimation on its root')
        data = self.skeleton_component[1].get('skeletonDataAsset') or {}
        unit = (read(data.get('m_PathID')) or (None, {}))[1].get('scale') if data.get('m_FileID', 0) == 0 else None
        if not isinstance(unit, (int, float)) or unit <= 0:
            raise LayerError('the skeleton has no scale')
        self.unit = float(unit)
        # spine-unity looks each separator name up in the skeleton and skips the ones it lacks.
        self.separators = []
        for name in self.skeleton_component[1].get('separatorSlotNames') or []:
            if isinstance(name, str) and name in self.slots and name not in self.separators:
                self.separators.append(name)
        self.action_of = {}
        for entry in self.controller.get('_particles') or []:
            ref = (entry or {}).get('particle') or {}
            if ref.get('m_FileID', 0) == 0 and ref.get('m_PathID') and entry.get('action') in ACTIONS:
                self.action_of[ref['m_PathID']] = ACTIONS[entry['action']]
        self.triggered = {r.get('m_PathID') for r in self.controller.get('_animators') or [] if isinstance(r, dict) and r.get('m_FileID', 0) == 0}
        # The scenes the controller's triggers switch its _animators into, by Spine animation name.
        self.state_scenes = {}
        for animation in TRIGGERS:
            if animation == 'Idle' or not self.triggered:
                continue
            scene = ec._Scene(root_go, read, choose=lambda pid, an, ctrl, n, a=animation: self._choose_state(pid, an, ctrl, n, a))
            if scene.switched:
                self.state_scenes[animation] = scene
        self.textures = {}  # texture path id -> index
        self.texture_images = []
        self.texture_info = []
        self.omitted = {'particles': 0, 'trails': 0, 'skinned': 0, 'hidden': 0, 'holders': len(self.controller.get('_holders') or []),
                        'custom': [], 'externalTexture': [], 'other': []}
        self.counts = {'static': 0, 'animated': 0, 'follow': 0, 'only': 0, 'states': 0, 'scroll': 0}
        self.masks = []

    # --- what each Animator plays

    @staticmethod
    def _default_speed(controller) -> float:
        try:
            constant = controller['m_Controller']
            machine = constant['m_StateMachineArray'][constant['m_LayerArray'][0]['data']['m_StateMachineIndex']]['data']
            return float(_state(controller, machine['m_DefaultState']).get('m_Speed', 1.0) or 1.0)
        except (KeyError, IndexError, TypeError):
            return 1.0

    def _choose_default(self, animator_id, animator, controller, clip_count):
        """An enabled Animator plays its default state (at its speed); a disabled one plays nothing."""
        if not animator.get('m_Enabled', 1):
            return False
        if clip_count <= 1:
            return None, self._default_speed(controller)
        return ec.default_state_clips(controller, clip_count), self._default_speed(controller)

    def _choose_state(self, animator_id, animator, controller, clip_count, animation):
        """While `animation` plays, the controller's _animators are in the state its trigger leads to."""
        if animator_id in self.triggered and animator.get('m_Enabled', 1):
            states = trigger_states(controller, clip_count)
            if animation in states and states[animation] != states.get(None):
                return states[animation][0], states[animation][1], True
        return self._choose_default(animator_id, animator, controller, clip_count)

    # --- helpers

    def name(self, tr) -> str:
        return self.scene.tree(self.scene.go_of[tr], 'GameObject').get('m_Name', '')

    def chain(self, tr) -> list:
        return self.scene.chain(tr)  # root first

    def relative(self, scene, tr, t):
        """tr's matrix relative to the skeleton (the root), in Unity units."""
        return ec.multiply(ec.inverse(scene.world(self.root, t)), scene.world(tr, t))

    def omit(self, bucket: str, name: str, reason: str):
        self.omitted[bucket].append({'name': name, 'reason': reason})

    def renderer_tree(self, tr) -> dict:
        return next((tree for kind, _, tree in _component_trees(self.scene, self.scene.go_of[tr]) if kind == 'MeshRenderer'), {})

    def group(self, tr):
        """(Spine animation, group transform) of the controller's _particles entry the layer is in."""
        for x in reversed(self.chain(tr)):
            if self.scene.go_of[x] in self.action_of:
                return self.action_of[self.scene.go_of[x]], x
        return None, None

    def switched_links(self, tr) -> list:
        """The chain links whose own active flag counts: below the action group's root (the controller
        switches the group itself with its animation)."""
        links = self.chain(tr)
        _, group = self.group(tr)
        return links[links.index(group) + 1:] if group is not None else links

    def static_active(self, tr) -> bool:
        if not self.renderer_tree(tr).get('m_Enabled', 1):
            return False
        return all(self.scene.tree(self.scene.go_of[link], 'GameObject').get('m_IsActive', 1) for link in self.switched_links(tr))

    def toggled(self, tr) -> bool:
        """Whether a clip (of the default states, or of a state the controller triggers) switches this
        renderer or a GameObject on its chain."""
        for scene in (self.scene, *self.state_scenes.values()):
            if any(scene.float_curves.get((link, ec.GAMEOBJECT, ec.IS_ACTIVE)) for link in self.chain(tr)) or \
                    scene.float_curves.get((tr, ec.RENDERER, ec.ENABLED)):
                return True
        return False

    def moves(self, tr) -> bool:
        return any(self.scene.transform_curves.get((link, a)) for link in self.chain(tr) for a in (ec.POSITION, ec.ROTATION, ec.EULER, ec.SCALE))

    # --- the skeleton's parts

    def parts(self) -> list:
        """The skeleton's renderers: one per SkeletonPartsRenderer, part k drawing the slots between the
        k-th and the next separator slot met in the current draw order (a separator starts its part).
        Renderers beyond the separators draw nothing and slots beyond the renderers are not drawn, as in
        SkeletonRenderSeparator. Without parts renderers, the root renderer draws everything (part 0)."""
        entries = []
        part_transforms = []
        for path_id in self.parts_renderers:
            entry = self.read(path_id)
            if not entry:
                raise LayerError('a SkeletonPartsRenderer cannot be read')
            tr = self.scene.transform_of.get(entry[1]['m_GameObject']['m_PathID'])
            if tr is None:
                raise LayerError('a SkeletonPartsRenderer is outside the prefab')
            part_transforms.append(tr)
        if part_transforms:
            for index, tr in enumerate(part_transforms[:len(self.separators) + 1]):
                renderer = self.renderer_tree(tr)
                entries.append({'part': index,
                                'sort': (renderer.get('m_SortingLayerID', 0), renderer.get('m_SortingOrder', 0), SPINE_QUEUE,
                                         -self.relative(self.scene, tr, 0.0)[2][3], self.walk[tr]), 'transform': tr})
        else:
            self.separators = []
            renderer = next((tree for kind, _, tree in _component_trees(self.scene, self.root_go) if kind == 'MeshRenderer'), {})
            entries.append({'part': 0,
                            'sort': (renderer.get('m_SortingLayerID', 0), renderer.get('m_SortingOrder', 0), SPINE_QUEUE, 0.0, self.walk[self.root]),
                            'transform': self.root})
        for entry in entries:
            if entry['sort'][0] != 0:
                raise LayerError(f'the skeleton draws in sorting layer {entry["sort"][0]}')
        return entries

    # --- one renderer

    def follower_on_chain(self, tr):
        """The nearest BoneFollower between the layer and the root: (transform, fields) or None."""
        found = None
        x = tr
        while x is not None and x != self.root:
            for kind, _, tree in _component_trees(self.scene, self.scene.go_of[x]):
                if kind == 'MonoBehaviour' and tree and 'boneName' in tree and tree.get('m_Enabled', 1):
                    if found is not None:
                        raise LayerError('bone followers inside bone followers')
                    target = (tree.get('skeletonRenderer') or {}).get('m_PathID')
                    if target != self.skeleton_component[0]:
                        raise LayerError('follows a bone of another skeleton')
                    found = (x, tree)
            x = self.scene.parent[x]
        return found

    def scripts(self, tr):
        """(UV scroll [u, v] in Unity UV units per second or None, delay in seconds) from the scripts on
        the layer and its chain; LayerError for a script whose effect is not known."""
        scroll, delay = None, 0.0
        for x in self.chain(tr):
            if x == self.root:
                continue
            for kind, _, tree in _component_trees(self.scene, self.scene.go_of[x]):
                if kind != 'MonoBehaviour' or not tree or not tree.get('m_Enabled', 1):
                    continue
                fields = {k for k in tree if not k.startswith('m_')}
                if 'boneName' in fields:
                    continue
                if fields == {'_delayTime'}:
                    # Shows its object this long after it is switched on (the action's start).
                    delay += float(tree.get('_delayTime') or 0.0)
                    continue
                if {'xspeed', 'yspeed'} <= fields:
                    if x != tr:
                        continue  # a UV scroll script moves its own renderer's material
                    if any('_MainTex' in str(s) for s in tree.get('extraMapSettings') or []):
                        raise LayerError('UV scroll script with extra main-texture settings')
                    speed = (float(tree.get('xspeed') or 0.0), float(tree.get('yspeed') or 0.0))
                    if any(speed) and not tree.get('protectMainUV', 0):
                        if not tree.get('keepInitOffset', 1):
                            raise LayerError('UV scroll script that restarts the offset')
                        scroll = list(speed)
                    continue
                if '_rotateTex1' in fields:
                    raise LayerError('UV rotation script')
                raise LayerError('script (' + ', '.join(sorted(fields)[:4]) + ')')
        return scroll, delay

    def check_curves(self, scene, tr, look_: Look, follower, material: Material):
        """Every curve that touches this layer must be one the export reproduces."""
        colour_hash = ec.crc(look_.color_property) & 0x0FFFFFFF
        known = {colour_hash, ec.crc('_MainTex_ST') & 0x0FFFFFFF}
        if look_.color_property == '_MainColor':
            known.add(ec.crc('_Opacity') & 0x0FFFFFFF)
        for (target, type_id, attribute) in scene.float_curves:
            if target != tr:
                continue
            if (type_id == ec.GAMEOBJECT and attribute == ec.IS_ACTIVE) or (type_id == ec.RENDERER and attribute == ec.ENABLED):
                continue
            if type_id == ec.RENDERER and ec.material_binding(attribute)[0] in known:
                continue
            if type_id == ec.RENDERER:
                raise LayerError(f'animated material property {property_name(material, ec.material_binding(attribute)[0])}')
            raise LayerError(f'animated component (type {type_id})')
        if follower is not None:
            ftr, fields = follower
            if not fields.get('followXYPosition') and scene.transform_curves.get((ftr, ec.POSITION)):
                raise LayerError('bone follower that keeps an animated position')
            if not fields.get('followBoneRotation') and (scene.transform_curves.get((ftr, ec.ROTATION)) or scene.transform_curves.get((ftr, ec.EULER))):
                raise LayerError('bone follower that keeps an animated rotation')
            links = self.chain(tr)
            for link in links[:links.index(ftr)]:
                for attribute in (ec.ROTATION, ec.EULER, ec.SCALE):
                    if scene.transform_curves.get((link, attribute)):
                        raise LayerError('bone follower under an animated parent')
            if not fields.get('followXYPosition'):
                for link in links[:links.index(ftr)]:
                    if scene.transform_curves.get((link, ec.POSITION)):
                        raise LayerError('bone follower under an animated parent')

    def clips_of(self, scene, tr, follower) -> list:
        """The clips that move, tint or switch this layer."""
        clips = {}

        def note(entries):
            for clip, _ in entries or []:
                clips[id(clip)] = clip
        links = self.chain(tr)
        below = links[links.index(follower[0]):] if follower else links
        for link in below:
            for attribute in (ec.POSITION, ec.ROTATION, ec.EULER, ec.SCALE):
                if follower and link == follower[0] and attribute != ec.SCALE:
                    continue  # the follower's own position and rotation come from its bone or stay
                note(scene.transform_curves.get((link, attribute)))
        for link in self.switched_links(tr):
            note(scene.float_curves.get((link, ec.GAMEOBJECT, ec.IS_ACTIVE)))
        for (target, type_id, attribute), entries in scene.float_curves.items():
            if target == tr and type_id == ec.RENDERER:
                note(entries)
        return list(clips.values())

    def sample(self, scene, tr, look_: Look, follower, material: Material, static: bool = False):
        """Frames [t, a, b, c, d, tx, ty, r, g, b, alpha, active, su, ou, sv, ov] (the matrix to skeleton
        units, or to the follower's units under a bone follower; colour with the x2 tint gain; active
        0/1; the UV map on the exported UVs) at FPS over the clips that drive the layer, and the timeline:
        (frames, length, loop, loopFrom, animated). Only t = 0 when nothing animates it or `static`."""
        self.check_curves(scene, tr, look_, follower, material)
        clips = [] if static else self.clips_of(scene, tr, follower)
        looping = [(c.stop - c.start) / c.speed for c in clips if c.loop and c.stop > c.start]
        once = [(c.stop - c.start) / c.speed for c in clips if not (c.loop and c.stop > c.start)]
        prelude = max(once, default=0.0)
        if looping:
            period = _lcm_period(looping)
            if period is None:
                raise LayerError('clips of unrelated lengths loop together')
            loop_from = math.ceil(prelude / period - 1e-6) * period if prelude > 0 else 0.0
            length = loop_from + period
        else:
            loop_from, length = 0.0, prelude
        count = int(round(length * FPS))
        times = [round(i / FPS, 4) for i in range(count + 1)] if count else [0.0]
        colour_hash = ec.crc(look_.color_property) & 0x0FFFFFFF
        st_hash = ec.crc('_MainTex_ST') & 0x0FFFFFFF
        opacity_hash = ec.crc('_Opacity') & 0x0FFFFFFF
        links = self.chain(tr)
        switched = self.switched_links(tr)
        below = links[links.index(follower[0]) + 1:] if follower else None
        renderer = self.renderer_tree(tr)
        st0 = look_.st
        opacity_animated = look_.color_property == '_MainColor' and bool(scene.float_curves.get((tr, ec.RENDERER, opacity_hash)))
        base_opacity = material.float('_Opacity', 1.0) or 1.0
        frames = []
        tilted = False  # depth reaches the screen: a rotation out of the plane
        for t in times:
            if below is not None:
                m = ec.IDENTITY
                for link in below:
                    m = ec.multiply(m, scene.local(link, t))
                factor = 1.0
            else:
                m = self.relative(scene, tr, t)
                factor = 1.0 / self.unit
            colour = [scene.float_value(tr, ec.RENDERER, colour_hash | ((4 + c) << 28), t, look_.color[c]) for c in range(4)]
            opacity = scene.float_value(tr, ec.RENDERER, opacity_hash, t, base_opacity) / base_opacity if opacity_animated else 1.0
            st = [scene.float_value(tr, ec.RENDERER, st_hash | ((4 + c) << 28), t, st0[c]) for c in range(4)]
            active = 1.0
            for link in switched:
                static_flag = 1.0 if self.scene.tree(self.scene.go_of[link], 'GameObject').get('m_IsActive', 1) else 0.0
                if scene.float_value(link, ec.GAMEOBJECT, ec.IS_ACTIVE, t, static_flag) < 0.5:
                    active = 0.0
            if scene.float_value(tr, ec.RENDERER, ec.ENABLED, t, 1.0 if renderer.get('m_Enabled', 1) else 0.0) < 0.5:
                active = 0.0
            # The exported UVs carry the material's own ST with v flipped; an animated ST maps them on:
            # u' = (u - o0) s / s0 + o and, in image space, v' = 1 - ((1 - v - o0v) sv / s0v + ov).
            su = st[0] / st0[0] if st0[0] else 1.0
            sv = st[1] / st0[1] if st0[1] else 1.0
            if abs(m[0][2]) > 1e-6 or abs(m[1][2]) > 1e-6:
                tilted = True
            frames.append([t, m[0][0] * factor, m[0][1] * factor, m[1][0] * factor, m[1][1] * factor, m[0][3] * factor, m[1][3] * factor,
                           2 * colour[0] * look_.rgb_scale, 2 * colour[1] * look_.rgb_scale, 2 * colour[2] * look_.rgb_scale,
                           2 * colour[3] * look_.alpha_scale * opacity, active,
                           su, st[2] - st0[2] * su, sv, 1 - sv - st[3] + st0[3] * sv])
        animated = len(frames) > 1 and any(f[1:] != frames[0][1:] for f in frames)
        self.tilted = tilted
        return frames, round(length, 4), bool(looping), round(loop_from, 4), animated

    def decimate(self, frames, extent: float, to_skeleton: float) -> list:
        """Drops frames a straight line reproduces, with tolerances from the layer's size so no vertex
        strays more than POSITION_TOLERANCE skeleton units."""
        matrix_tol = POSITION_TOLERANCE / max(extent * to_skeleton, 1e-6)
        move_tol = POSITION_TOLERANCE / to_skeleton
        tolerances = [matrix_tol] * 4 + [move_tol] * 2 + [COLOUR_TOLERANCE] * 4 + [0.01] + [UV_TOLERANCE] * 4
        kept = ec.decimate(frames, tolerances)
        return [[round(v, 6) if i in (1, 2, 3, 4, 12, 13, 14, 15) else round(v, 4) for i, v in enumerate(f)] for f in kept]

    def texture_index(self, texture: dict) -> int:
        tid = texture['id']
        if tid in self.textures:
            return self.textures[tid]
        tree = self.read(tid)[1]
        image = self.texture_of(tid)
        alpha = texture_alpha(image, self.classify_texture)
        settings = tree.get('m_TextureSettings') or {}
        modes = ['repeat', 'clamp', 'mirror', 'mirror-once']
        wrap_u = settings.get('m_WrapU', 0)
        wrap_v = settings.get('m_WrapV', wrap_u)
        wrap = [modes[w] if isinstance(w, int) and 0 <= w < 4 else 'repeat' for w in (wrap_u, wrap_v)]
        if 'mirror-once' in wrap:
            raise LayerError('texture wraps mirror-once')
        box = image.getchannel('A').point(lambda a: 255 if a >= OPAQUE_ALPHA else 0).getbbox()
        if box is None:
            raise LayerError('nothing visible (its texture is transparent)')
        # The part of the texture that shows, in image-space UV: the frame is fitted to it, not to the
        # transparent margin around it.
        opaque = [round(box[0] / image.width, 5), round(box[1] / image.height, 5), round(box[2] / image.width, 5), round(box[3] / image.height, 5)]
        index = len(self.texture_images)
        self.textures[tid] = index
        self.texture_images.append(image)
        self.texture_info.append({'name': tree.get('m_Name', ''), 'width': image.width, 'height': image.height, 'wrap': wrap,
                                  'opaque': opaque, 'measured': alpha})
        return index

    def mesh(self, tr) -> dict:
        mesh_filter = next((tree for kind, _, tree in _component_trees(self.scene, self.scene.go_of[tr]) if kind == 'MeshFilter'), None)
        ref = (mesh_filter or {}).get('m_Mesh') or {}
        if not ref.get('m_PathID'):
            raise LayerError('no mesh')
        if ref.get('m_FileID', 0) == 0:
            return self.mesh_of(ref['m_PathID'])
        cab = self.external_of(ref)
        if cab == BUILTIN_RESOURCES and ref['m_PathID'] == BUILTIN_QUAD:
            return QUAD_MESH
        raise LayerError(f'mesh in {cab} ({ref["m_PathID"]})')

    def layer(self, tr) -> list[dict]:
        """The draw entries of one MeshRenderer (one per submesh with a material), with their sort keys."""
        name = self.name(tr)
        renderer = self.renderer_tree(tr)
        materials = [r for r in renderer.get('m_Materials') or [] if isinstance(r, dict)]
        if not materials:
            raise LayerError('no material')
        if renderer.get('m_SortingLayerID', 0) != 0:
            raise LayerError(f'sorting layer {renderer.get("m_SortingLayerID")}')
        only, _ = self.group(tr)
        scroll_script, delay = self.scripts(tr)
        follower = self.follower_on_chain(tr)
        mesh = self.mesh(tr)
        if len(mesh['vertices']) > MAX_VERTICES:
            raise LayerError(f'{len(mesh["vertices"])} vertices')
        submeshes = mesh['submeshes']
        out = []
        # One draw per submesh with its material; materials beyond the submeshes draw the last submesh
        # again, each as another pass over it (Unity's multi-material renderers).
        for index, ref in enumerate(materials):
            triangles = submeshes[min(index, len(submeshes) - 1)]
            label = name if len(materials) == 1 else f'{name} ({index})'
            if ref.get('m_FileID', 0) != 0 or not ref.get('m_PathID'):
                self.omit('other', label, 'material in another bundle')
                continue
            material = read_material(self.read(ref['m_PathID'])[1], read=self.read, external_of=self.external_of, shaders=self.shaders)
            try:
                look_ = look(material)
            except LayerError as error:
                self.omit('custom', label, str(error))
                continue
            try:
                out.append(self.entry(tr, label, look_, material, triangles, mesh, follower, only, delay, scroll_script))
            except LayerError as error:
                if str(error) == 'hidden':
                    self.omitted['hidden'] += 1
                else:
                    self.omit('externalTexture' if str(error).startswith('texture in ') else 'other', label, str(error))
            except ec.CameraError as error:
                self.omit('other', label, str(error))
        return out

    def follow_of(self, follower) -> tuple[dict, float, float]:
        """model `follow` for a BoneFollower, how many skeleton units one of its units is, and its z."""
        ftr, fields = follower
        parent = self.scene.parent[ftr]
        immediate = parent == self.root
        root_q = self.scene.rotation(self.root, 0.0)
        inv_root_q = (-root_q[0], -root_q[1], -root_q[2], root_q[3])
        if immediate:
            # spine-unity's local path: the follower's local position and rotation are the bone's.
            k = [[1.0, 0.0], [0.0, 1.0]]
            mirrored = False
        else:
            # The world path: its world rotation is set outright (skeleton's + bone's, negated under a
            # mirrored parent), so relative to the skeleton it is parent x inverse(parent rotation) x
            # R(bone) x its scale.
            p = self.relative(self.scene, parent, 0.0)
            mirrored = (p[0][0] * p[1][1] - p[0][1] * p[1][0]) < 0
            rel_q = ec.quaternion_multiply(inv_root_q, self.scene.rotation(parent, 0.0))
            pr = ec.multiply(p, ec.trs((0, 0, 0), (-rel_q[0], -rel_q[1], -rel_q[2], rel_q[3]), (1, 1, 1)))
            if abs(pr[0][2]) > 1e-6 or abs(pr[1][2]) > 1e-6:
                raise LayerError('bone follower under a parent turned out of the plane')
            k = [[pr[0][0], pr[0][1]], [pr[1][0], pr[1][1]]]
        own = self.relative(self.scene, ftr, 0.0)
        angle = 0.0
        if not fields.get('followBoneRotation'):
            # It keeps its own rotation: relative to the skeleton, its world rotation.
            angle = _z_angle(ec.quaternion_multiply(inv_root_q, self.scene.rotation(ftr, 0.0)))
        follow = {'bone': fields.get('boneName'), 'xy': bool(fields.get('followXYPosition')), 'rotation': bool(fields.get('followBoneRotation')),
                  'localScale': bool(fields.get('followLocalScale')), 'mirrored': mirrored,
                  'parent': [round(k[0][0] / self.unit, 6), round(k[0][1] / self.unit, 6), round(k[1][0] / self.unit, 6), round(k[1][1] / self.unit, 6)],
                  'position': [round(own[0][3] / self.unit, 3), round(own[1][3] / self.unit, 3)], 'angle': round(angle, 4)}
        if not isinstance(follow['bone'], str) or not follow['bone']:
            raise LayerError('bone follower without a bone')
        return follow, _matrix_scale([[v / self.unit for v in row] for row in k]), own[2][3]

    def entry(self, tr, label, look_: Look, material: Material, triangles, mesh, follower, only, delay, scroll_script) -> dict:
        if look_.blend not in ('alpha', 'add'):
            raise LayerError(f'blend {look_.blend}')
        if look_.texture is None:
            raise LayerError('no main texture')
        if 'external' in look_.texture:
            raise LayerError(f'texture in {look_.texture["external"]}')
        if not triangles or len(triangles) % 3:
            raise LayerError('no triangles')
        if scroll_script and any(look_.scroll):
            raise LayerError('UV scroll from both its shader and a script')
        flat = all(abs(v[2]) <= 1e-6 for v in mesh['vertices'])
        frames, length, loop, loop_from, animated = self.sample(self.scene, tr, look_, follower, material)
        tilted = self.tilted
        if not animated:
            frames, length, loop, loop_from, _ = self.sample(self.scene, tr, look_, follower, material, static=True)
        # The states the controller's triggers put its _animators in (Interact, Special...), where they
        # play something else for this layer.
        states = {}
        for animation_name, scene in self.state_scenes.items():
            other = self.sample(scene, tr, look_, follower, material)
            tilted = tilted or self.tilted
            if other[0] != frames and (other[4] or other[0][0][1:] != frames[0][1:]):
                states[animation_name] = other
        first = frames[0]
        if not animated and first[11] < 0.5 and not any(any(f[11] >= 0.5 for f in st[0]) for st in states.values()):
            raise LayerError('hidden')
        animated = animated or bool(states)
        if (animated or follower) and not flat and tilted:
            raise LayerError('a mesh that is not flat turns out of the plane')
        texture = self.texture_index(look_.texture)
        used = sorted(set(triangles))
        remap = {v: i for i, v in enumerate(used)}
        tris = [remap[i] for i in triangles]
        st = look_.st
        uvs = []
        for v in used:
            u0, v0 = mesh['uv'][v][:2]
            uvs += [u0 * st[0] + st[2], 1 - (v0 * st[1] + st[3])]
        colors = [round(float(c), 4) for v in used for c in mesh['colors'][v][:4]] if mesh.get('colors') else None
        colour = None
        if not animated:
            colour = [round(c, 4) for c in first[7:11]]
            uvs = [uvs[i] * first[12] + first[13] if i % 2 == 0 else uvs[i] * first[14] + first[15] for i in range(len(uvs))]
        uvs = [round(x, 6) for x in uvs]
        follow, to_skeleton, z = None, 1.0, 0.0
        if follower is not None:
            follow, to_skeleton, z = self.follow_of(follower)
            self.counts['follow'] += 1
        if (animated or follower is not None) and look_.cull:
            raise LayerError(f'culls {"back" if look_.cull == 2 else "front"} faces while it moves')
        animation = None
        if animated:
            local = [mesh['vertices'][v] for v in used]
            extent = max((math.hypot(p[0], p[1]) for p in local), default=1.0) or 1.0
            # Skeleton units per unit of the frames' matrix target: 1 (skeleton), or the follower's.
            scale = to_skeleton if follower is not None else 1.0
            animation = {'length': length, 'loop': loop, 'loopFrom': loop_from, 'frames': self.decimate(frames, extent, scale)}
            if states:
                animation['states'] = {name_: {'length': st[1], 'loop': st[2], 'loopFrom': st[3], 'frames': self.decimate(st[0], extent, scale)}
                                       for name_, st in sorted(states.items())}
                self.counts['states'] += 1
            vertices = [round(c, 5) for p in local for c in p[:2]]
            if follower is None:
                z = self.relative(self.scene, tr, 0.0)[2][3]
            self.counts['animated'] += 1
        elif follower is not None:
            links = self.chain(tr)
            below = ec.IDENTITY
            for link in links[links.index(follower[0]) + 1:]:
                below = ec.multiply(below, self.scene.local(link, 0.0))
            vertices = [round(c, 5) for v in used for c in ec.transform_point(below, mesh['vertices'][v])[:2]]
        else:
            m = self.relative(self.scene, tr, 0.0)
            points = [ec.transform_point(m, mesh['vertices'][v]) for v in used]
            if look_.cull:
                kept = []
                for i in range(0, len(tris), 3):
                    a, b, c = (points[tris[i + j]] for j in range(3))
                    area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                    # Unity's front faces wind clockwise on screen: Cull Back keeps the clockwise ones.
                    if (look_.cull == 2 and area <= 0) or (look_.cull == 1 and area >= 0):
                        kept += tris[i:i + 3]
                if not kept:
                    raise LayerError('every face culled')
                tris = kept
            vertices = [round(p[k] / self.unit, 3) for p in points for k in (0, 1)]
            z = sum(p[2] for p in points) / len(points)
            self.counts['static'] += 1
        speed = scroll_script or (look_.scroll if any(look_.scroll) else None)
        scroll = [round(speed[0], 6), round(-speed[1], 6)] if speed else None  # image space flips v
        if scroll:
            self.counts['scroll'] += 1
        if only:
            self.counts['only'] += 1
        renderer = self.renderer_tree(tr)
        layer = {'name': label, 'blend': look_.blend, 'texture': texture, 'color': colour, 'vertices': vertices, 'uvs': uvs, 'colors': colors,
                 'triangles': tris, 'follow': follow, 'animation': animation, 'scroll': scroll, 'only': only, 'delay': round(delay, 4)}
        return {'layer': layer, 'sort': (renderer.get('m_SortingLayerID', 0), renderer.get('m_SortingOrder', 0), look_.queue, -z, self.walk[tr])}

    def run(self) -> LayerExport:
        entries = self.parts()
        part_transforms = {e['transform'] for e in entries} | {self.root}
        part_transforms |= {self.scene.transform_of.get((self.read(p) or (None, {}))[1].get('m_GameObject', {}).get('m_PathID')) for p in self.parts_renderers}
        for tr in self.order:
            go = self.scene.go_of[tr]
            components = _component_trees(self.scene, go)
            kinds = {kind for kind, _, _ in components}
            name = self.name(tr)
            for kind, _, tree in components:
                if kind == 'MeshRenderer' and tr in part_transforms:
                    continue
                if kind == 'ParticleSystemRenderer' or (kind == 'MeshRenderer' and 'ParticleSystem' in kinds):
                    self.omitted['particles'] += 1
                elif kind in ('TrailRenderer', 'LineRenderer'):
                    self.omitted['trails'] += 1
                elif kind == 'SkinnedMeshRenderer':
                    self.omitted['skinned'] += 1
                elif kind in ('SpriteRenderer', 'BillboardRenderer', 'CanvasRenderer'):
                    self.omit('other', name, kind)
                elif kind == 'MeshRenderer':
                    self.note_mask(tr, tree)
                    if any(k == 'MonoBehaviour' and t and 'skeletonDataAsset' in t for k, _, t in components):
                        self.omit('other', name, 'nested skeleton')
                    elif not self.static_active(tr) and not self.toggled(tr):
                        self.omitted['hidden'] += 1
                    else:
                        try:
                            entries += self.layer(tr)
                        except LayerError as error:
                            if str(error) == 'hidden':
                                self.omitted['hidden'] += 1
                            else:
                                self.omit('externalTexture' if str(error).startswith('texture in ') else 'other', name, str(error))
                        except ec.CameraError as error:
                            self.omit('other', name, str(error))
        entries.sort(key=lambda e: e['sort'])
        entries = self.unmasked(entries)
        draw = [{'part': e['part']} if 'part' in e else {'layer': e['layer']} for e in entries]
        # Textures only layers still draw, renumbered in order.
        used = sorted({d['layer']['texture'] for d in draw if 'layer' in d})
        renumber = {old: new for new, old in enumerate(used)}
        for d in draw:
            if 'layer' in d:
                d['layer']['texture'] = renumber[d['layer']['texture']]
        self.texture_images = [self.texture_images[i] for i in used]
        self.texture_info = [self.texture_info[i] for i in used]
        textures = [{'file': f'layer{i}.webp', 'width': info['width'], 'height': info['height'], 'wrap': info['wrap'], 'opaque': info['opaque']}
                    for i, info in enumerate(self.texture_info)]
        for bucket in ('custom', 'externalTexture', 'other'):
            self.omitted[bucket].sort(key=lambda item: (item['name'], item['reason']))
        document = {'schemaVersion': SCHEMA_VERSION, 'textures': textures, 'bounds': None, 'separators': self.separators, 'draw': draw,
                    'omitted': self.omitted}
        layers = [d['layer'] for d in draw if 'layer' in d]
        self.counts = {'layers': len(layers), 'parts': sum(1 for d in draw if 'part' in d),
                       'static': sum(1 for l in layers if not l['animation'] and not l['follow']),
                       'animated': sum(1 for l in layers if l['animation']), 'follow': sum(1 for l in layers if l['follow']),
                       'only': sum(1 for l in layers if l['only']), 'states': sum(1 for l in layers if l['animation'] and l['animation'].get('states')),
                       'scroll': sum(1 for l in layers if l['scroll'])}
        return LayerExport(document, self.texture_images, self.texture_info, self.counts)

    # --- masks

    def note_mask(self, tr, renderer: dict):
        """Remembers a visible renderer drawn with the Erase mask shader: it paints over what was drawn
        before it (alpha from its texture), so those layers do not look as the site would draw them."""
        names = []
        for ref in renderer.get('m_Materials') or []:
            if isinstance(ref, dict) and ref.get('m_FileID', 0) == 0 and ref.get('m_PathID'):
                entry = self.read(ref['m_PathID'])
                if entry and entry[0] == 'Material':
                    shader = read_material(entry[1], read=self.read, external_of=self.external_of, shaders=self.shaders).shader
                    names.append(shader.name if shader else '')
        if not any('/Mask/Erase' in n for n in names) or not (self.static_active(tr) or self.toggled(tr)):
            return
        bounds = None  # everywhere, unless its mesh's box says otherwise
        mesh_filter = next((tree for kind, _, tree in _component_trees(self.scene, self.scene.go_of[tr]) if kind == 'MeshFilter'), None)
        ref = (mesh_filter or {}).get('m_Mesh') or {}
        corners = None
        if ref.get('m_PathID') and ref.get('m_FileID', 0) == 0:
            box = ((self.read(ref['m_PathID']) or (None, {}))[1] or {}).get('m_LocalAABB')
            if box:
                c, e = box['m_Center'], box['m_Extent']
                corners = [(c['x'] + sx * e['x'], c['y'] + sy * e['y'], c['z'] + sz * e['z']) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
        elif ref.get('m_PathID') == BUILTIN_QUAD and self.external_of(ref) == BUILTIN_RESOURCES:
            corners = [(x, y, 0.0) for x in (-0.5, 0.5) for y in (-0.5, 0.5)]
        if corners is not None and not self.moves(tr):
            m = self.relative(self.scene, tr, 0.0)
            points = [ec.transform_point(m, p) for p in corners]
            bounds = (min(p[0] for p in points) / self.unit, min(p[1] for p in points) / self.unit,
                      max(p[0] for p in points) / self.unit, max(p[1] for p in points) / self.unit)
        renderer_sort = (renderer.get('m_SortingLayerID', 0), renderer.get('m_SortingOrder', 0))
        self.masks.append({'name': self.name(tr), 'sort': renderer_sort, 'walk': self.walk[tr], 'bounds': bounds})

    def layer_bounds(self, layer: dict):
        """A layer's box in skeleton units over its timeline, or None when a bone places it (unknown here)."""
        if layer['follow']:
            return None
        v = layer['vertices']
        frames = [f[1:7] for f in layer['animation']['frames']] if layer['animation'] else [[1, 0, 0, 1, 0, 0]]
        for state in (layer['animation'] or {}).get('states', {}).values():
            frames += [f[1:7] for f in state['frames']]
        xs, ys = [], []
        for a, b, c, d, tx, ty in frames:
            for i in range(0, len(v), 2):
                xs.append(a * v[i] + b * v[i + 1] + tx)
                ys.append(c * v[i] + d * v[i + 1] + ty)
        return min(xs), min(ys), max(xs), max(ys)

    def unmasked(self, entries: list) -> list:
        """Leaves out the layers an Erase mask drawn after them overlaps: drawn without it they would
        show what the game paints over."""
        if not self.masks:
            return entries
        kept = []
        for entry in entries:
            if 'layer' in entry:
                box = self.layer_bounds(entry['layer'])
                for mask in self.masks:
                    if (mask['sort'], mask['walk']) <= (entry['sort'][:2], entry['sort'][4]):
                        continue  # drawn before the layer: it paints over nothing of it
                    m = mask['bounds']
                    if m is None or box is None or (box[0] < m[2] and m[0] < box[2] and box[1] < m[3] and m[1] < box[3]):
                        self.omit('other', entry['layer']['name'], f'under the mask {mask["name"]} (Erase), which is not drawn')
                        break
                else:
                    kept.append(entry)
                continue
            kept.append(entry)
        return kept


def export_layers(root_go: int, read, *, mesh_of: Callable, texture_of: Callable, classify_texture: Callable,
                  external_of: Callable, shaders: dict, slots: list[str]) -> LayerExport:
    """The layers of the illustration prefab whose root GameObject is `root_go`.

    read(path_id) -> (type name, typetree) | None for any object in the bundle; mesh_of(path_id) ->
    {'vertices': [(x, y, z)], 'uv': [(u, v)], 'colors': [(r, g, b, a) in 0-1] | None, 'submeshes':
    [[index, ...]]}; texture_of(path_id) -> PIL image (RGBA as shipped); classify_texture(image) ->
    l2d.classify_alpha's result; external_of(ref) -> the CAB name (or 'unity default resources') a
    reference with m_FileID != 0 points into; shaders: shader_table() of the shared shader bundle;
    slots: the skeleton's slot names (the Spine runtime's reading), which separator names must match.
    """
    return _Exporter(root_go, read, mesh_of=mesh_of, texture_of=texture_of, classify_texture=classify_texture,
                     external_of=external_of, shaders=shaders, slots=slots).run()


# ---------------------------------------------------------------------------
# UnityPy glue (real bundles)


def bundle_readers(env, objects: dict):
    """mesh_of, texture_of and external_of for a UnityPy environment (`objects`: path id -> object)."""
    serialized = [sf for bf in env.files.values() for sf in getattr(bf, 'files', {}).values() if hasattr(sf, 'externals')]
    if not serialized:
        serialized = [sf for sf in env.files.values() if hasattr(sf, 'externals')]
    externals = [(getattr(e, 'path', '') or '').split('/')[-1] for e in serialized[0].externals] if serialized else []

    def external_of(ref) -> str:
        index = ref.get('m_FileID', 0)
        if 1 <= index <= len(externals):
            return externals[index - 1].lower() if externals[index - 1].lower() == BUILTIN_RESOURCES else externals[index - 1]
        return f'file {index}'

    def mesh_of(path_id: int) -> dict:
        from UnityPy.helpers.MeshHelper import MeshHandler

        mesh = objects[path_id].read()
        handler = MeshHandler(mesh)
        handler.process()
        if not handler.m_Vertices:
            raise LayerError('mesh without vertices')
        if not handler.m_UV0:
            raise LayerError('mesh without UVs')
        submeshes = []
        for submesh, triangles in zip(mesh.m_SubMeshes, handler.get_triangles()):
            base = getattr(submesh, 'baseVertex', 0) or 0
            submeshes.append([index + base for triangle in triangles for index in triangle])
        colours = None
        if handler.m_Colors:
            colours = [tuple((c / 255.0 if isinstance(c, int) else float(c)) for c in colour) for colour in handler.m_Colors]
        vertices = [tuple(float(x) for x in (list(v) + [0.0, 0.0])[:3]) for v in handler.m_Vertices]
        return {'vertices': vertices, 'uv': [tuple(float(x) for x in uv[:2]) for uv in handler.m_UV0], 'colors': colours, 'submeshes': submeshes}

    def texture_of(path_id: int):
        return objects[path_id].read().image.convert('RGBA')

    return mesh_of, texture_of, external_of


def cab_name(env) -> str:
    """The CAB name of a bundle's (first) serialized file, as other bundles' externals name it."""
    for bf in env.files.values():
        for name, sf in getattr(bf, 'files', {}).items():
            if hasattr(sf, 'externals'):
                return name
    raise LayerError('bundle holds no serialized file')


def shader_table_of_bundle(data: bytes, unitypy) -> dict:
    """shader_table() of a shared shader bundle's bytes (the unpacked .ab)."""
    env = unitypy.load(data)
    cab = cab_name(env)
    return shader_table(cab, [(o.path_id, o.type.name, o.read_typetree) for o in env.objects])
