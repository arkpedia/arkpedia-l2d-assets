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
from dataclasses import dataclass, field, replace
from typing import Callable

import effects
import entrance_camera as ec

SCHEMA_VERSION = 1
# model.json `layersVersion`: what layers.json holds. 1 (or absent): plain layers only. 2: effect entries,
# exact upgrades, effectTextures and effectBounds, and textures from the shared FX bundles. 3: `tilted`
# entries (a mesh that turns in depth while it moves: its own 3D vertices and, per frame, the 2x4
# orthographic projection of its transform), animated scroll speeds as integrated `offset` parameters,
# Disturb2's animated noise and glow, and animated float and _ST properties read as the clips bind them.
# The sync re-exports the layers of a folder written by an older version (scripts/sync.py).
LAYERS_VERSION = 3
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
# A material property an Animator drives is bound as crc32(name) & 0x0FFFFFFF with its kind in the top
# four bits: 8 a float, 4-7 a colour's r, g, b, a, 0-3 a vector's x, y, z, w (a texture's _ST). Counted
# over every Renderer curve of the 88 Global bundles: 815 floats at 8, 2,221 _ST components at 0-3,
# 10,532 colour channels at 4-7. A vector property saved as a colour (Disturb2's _Noise1Param) binds as
# one, so a component is looked up both ways.
FLOAT_BINDING = 8
# The illustration is drawn by an orthographic camera (the controller's _cameraSize is its half height;
# no illustration prefab has a camera of its own), so a mesh's depth only decides its draw order and which
# of its faces are culled. Two frames' projections count as one when no vertex moves further apart than
# this (skeleton units) between them.
PROJECTION_TOLERANCE = 0.01
# Decimation tolerances: positions in skeleton units, colours in 0-1 (x2 tint scale), UV.
POSITION_TOLERANCE = 0.25
COLOUR_TOLERANCE = 0.004
UV_TOLERANCE = 0.0005
EFFECT_TOLERANCE = 0.0005  # an effect's animated parameters (amounts, tilings, intensities)
# An effect frames the view only where it shows over at least this share of its UVs at its first frame.
VISIBLE_COVERAGE = 0.1
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


@dataclass(frozen=True)
class RendererKind:
    """Which Renderer a draw comes from: its component type as the typetree names it, and the type id an
    AnimationClip binds its properties with (m_Enabled and the material properties). Every helper that
    reads a renderer or its clip bindings takes one; none assumes the MeshRenderer."""
    component: str
    binding: int


MESH = RendererKind('MeshRenderer', ec.RENDERER)
PARTICLE = RendererKind('ParticleSystemRenderer', ec.PARTICLE_RENDERER)


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


def read_material(tree: dict, *, read, external_of, shaders: dict, home: str | None) -> Material:
    """A Material typetree, read through the bundle that holds it: `read` and `external_of` are that bundle's,
    and `home` is its CAB name, or None for the outfit bundle itself. A texture of a material in a shared
    bundle (refs/fx/sharedbattle.ab...) that sits in that same bundle is named by `home`, so it is fetched from
    there and not looked up in the outfit bundle."""
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
                    if home is not None:
                        tex['external'] = home
                else:
                    tex = {'external': 'this bundle (not a Texture2D)'}
            else:
                tex = {'external': external_of(ref), 'id': ref['m_PathID']}
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


def binding_keys(prop: str, component: int | None = None) -> tuple:
    """The clip binding attributes a material property (component None: a float) can be animated under."""
    h = ec.crc(prop) & 0x0FFFFFFF
    if component is None:
        return (h | (FLOAT_BINDING << 28),)
    return (h | ((4 + component) << 28), h | (component << 28))


def declared(m: Material, hash28: int) -> bool:
    """Whether the material's shader declares the property a binding names (or the texture whose _ST it
    is). An Animator that drives a property the shader does not have changes nothing the game draws."""
    if m.shader is None or not m.shader.defaults:
        return True  # nothing to tell by: assume it does
    for name in m.shader.defaults:
        if isinstance(name, str) and hash28 in (ec.crc(name) & 0x0FFFFFFF, ec.crc(name + '_ST') & 0x0FFFFFFF):
            return True
    return False


def property_name(m: Material, hash28: int) -> str:
    """The material property a curve's attribute hash names (crc32 low 28 bits), or the hash."""
    defaults = list(m.shader.defaults) if m.shader else []
    for name in [*m.floats, *m.colors, *(f'{t}_ST' for t in m.textures), *defaults, *(f'{t}_ST' for t in defaults if isinstance(t, str))]:
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
    # What the site draws differently from the game, or None: a slight flow distortion drawn without
    # its wobble (FLOW_UNDISTORTED).
    approximated: str | None = None
    # The material's own _Opacity (Disturb/Ram), apart from alpha_scale: an animated _Opacity replaces it
    # rather than scaling it, so a fade in from 0 still shows.
    opacity: float = 1.0
    # The game's effect shader as data (effects.describe), or None for a plain material: the layer is an
    # `effect` entry. `exact`: the same for an approximated plain layer, which readers that know the
    # effect draw exactly (its `exact`).
    effect: dict | None = None
    exact: dict | None = None


# A flow-distortion layer whose texture moves at most this far is drawn without the distortion: the
# wobble is a few texels (Ines's backdrop in Under the Flaming Dome: 6 and 16 texels, Rosmontis's sky:
# 40; Eyjafjalla's window clouds: 0.06 UV), and leaving the layer out loses the backdrop itself.
# Stronger flows (0.1-0.3 UV, real motion) stay out until the shader is ported.
FLOW_UNDISTORTED = {'uv': 0.06, 'texels': 40.0}


def slight_flow(flow: dict) -> bool:
    """Whether a flow distortion (flow_distortion) is slight enough to draw the layer without it."""
    return flow['uv'] <= FLOW_UNDISTORTED['uv'] and 0 < flow['texels'] <= FLOW_UNDISTORTED['texels']


ANCHOR_DISTORTIONS = (('_DisturTex', '_IntensityU', '_IntensityV', None),
                      ('_DisturTex_02', '_IntensityU_02', '_IntensityV_02', '_ToggleUseDisturb2'))


def anchor_distorts(m: Material, texture: str) -> bool:
    """Whether an anchor distortion samples a texture (a moving offset) rather than nothing (a constant one)."""
    return bool(m.texture(texture) or ('_WEIGHT_ON' in m.keywords and m.texture('_WeightTex')))


# What one renderer's objects can throw when UnityPy cannot read them (line or point meshes, missing
# fields): that renderer is left out under omitted.other, never the whole model (README: layers.json).
UNREADABLE = (KeyError, TypeError, ValueError, AssertionError, IndexError, ArithmeticError)


def unreadable(error: Exception) -> str:
    return f'unreadable ({type(error).__name__}: {str(error)[:120]})'


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
        # The anchor shader adds both distortions at once (uv + (d1 - anchor1) * I1 + (d2 - anchor2) * I2),
        # before _MainTex_ST, so their sum moves the texture, scaled by its tiling.
        scale = (m.textures.get('_MainTex') or {}).get('scale', [1, 1])
        total_u = total_v = 0.0
        for texture, iu, iv, toggle in ANCHOR_DISTORTIONS:
            if toggle and not m.float(toggle):
                continue
            a, b = m.float(iu), m.float(iv)
            if (a or b) and anchor_distorts(m, texture):
                total_u += abs(a)
                total_v += abs(b)
        if total_u or total_v:
            return {'uv': round(max(total_u, total_v), 4),
                    'texels': round(max(total_u * (main.get('w') or 0) * abs(scale[0]), total_v * (main.get('h') or 0) * abs(scale[1])), 1)}
    return None


# Animated properties a plain layer reproduces itself (its colour, tiling and opacity).
PLAIN_ANIMATED = {'_TintColor', '_MainColor', '_MainTex_ST', '_Opacity'}


def look(m: Material, animated=frozenset()) -> Look:
    """The material as the site draws it, or LayerError with why it cannot be: plain (texture x 2 x vertex
    colour x tint), or with the game's effect shader as data (effects.describe) when its effect changes
    pixels. A slight flow distortion stays a plain, approximated layer that carries its exact effect.
    `animated`: the material properties an Animator drives on the renderer; one that only an effect
    reproduces (a dissolve's amount, a noise's tiling...) makes the layer an effect."""
    if m.shader is not None and m.shader.name in effects.SHADERS and set(animated) - PLAIN_ANIMATED:
        try:
            return effect_look(m, animated, custom=None)
        except effects.Unsupported as unsupported:
            raise LayerError(str(unsupported)) from None
    try:
        plain = plain_look(m)
    except LayerError:
        try:
            return effect_look(m, animated, custom=None)
        except effects.Unsupported as unsupported:
            raise LayerError(str(unsupported)) from None
    if plain.approximated:
        try:
            exact = effect_look(m, custom=None)
            # Its own tiling: the approximation's may hold the undistorted wobble's centre.
            plain.exact = {**exact.effect, 'st': exact.st} if exact.effect else None
        except effects.Unsupported:
            pass
    return plain


def effect_look(m: Material, animated=frozenset(), *, custom) -> Look:
    """A material with an effect the site reproduces (effects.describe), as a Look whose `effect` holds it,
    with `params` (effects.parameters) and `animated_paths`, the parameters an Animator drives. `custom`:
    None for a mesh layer, or the properties a particle's custom vertex inputs drive (effects.describe)."""
    effect = effects.describe(m, frozenset(animated), custom=custom)
    effect['params'] = effects.parameters(effect)
    effect['animated_paths'] = [path for path, (kind, props) in sorted(effect['params'].items()) if set(effects.property_names(kind, props)) & set(animated)]
    blend = BLENDS.get((int(m.factor(m.shader.src)), int(m.factor(m.shader.dst))), f'{m.factor(m.shader.src):g},{m.factor(m.shader.dst):g}')
    main = m.textures.get('_MainTex') or {'tex': None, 'scale': [1, 1], 'offset': [0, 0]}
    prop = effect['color_property']
    default = m.shader.defaults.get(prop, 0.5)
    color = list(m.colors.get(prop) or [default] * 4)
    speed = (effect.get('main') or {}).get('speed') or [0.0, 0.0]
    # Nothing but the particle maths (Dissolve(CustomData) without its dissolve, say): a plain layer,
    # as long as its main texture is bound (an unbound one is Unity's white, which only effects draw).
    plain = effect['family'] == effects.PARTICLE and main['tex'] and not (effect.get('distort') or effect.get('dissolve') or effect.get('edge')
                                                                          or effect.get('ramp') or effect.get('vertex') or effect['main'].get('fract')
                                                                          or effect['animated_paths'])
    return Look(blend=blend, texture=main['tex'], st=[*main['scale'], *main['offset']], color=color, color_property=prop,
                rgb_scale=effect.get('rgb_scale', 1.0), alpha_scale=effect.get('alpha_scale', 1.0), scroll=list(speed),
                cull=int(m.factor(m.shader.cull)), queue=m.queue, opacity=effect.get('opacity', 1.0), effect=None if plain else effect)


def plain_effect(look_: Look) -> dict:
    """A plain material as an effect with no stage (the particle maths alone), for a layer only an effect
    entry can carry (one culled as it is drawn)."""
    return {'family': effects.PARTICLE, 'plain': True, 'main': {'speed': list(look_.scroll), 'fract': False}, 'distort': None, 'dissolve': [],
            'edge': None, 'ramp': None, 'vertex': None, 'params': {}, 'animated_paths': []}


def plain_look(m: Material) -> Look:
    """The material as a plain layer, or LayerError with why it is not one."""
    if m.shader is None:
        raise LayerError(f'shader not found ({m.shader_ref})')
    raw = m.shader.name
    name = raw.replace('Hidden/', '', 1) if raw.startswith('Hidden/Torappu') else raw
    keywords = m.keywords
    main = m.textures.get('_MainTex') or {'tex': None, 'scale': [1, 1], 'offset': [0, 0]}
    st = [*main['scale'], *main['offset']]
    blend = BLENDS.get((int(m.factor(m.shader.src)), int(m.factor(m.shader.dst))), f'{m.factor(m.shader.src):g},{m.factor(m.shader.dst):g}')
    cull = int(m.factor(m.shader.cull))

    approximated = None

    def plain(color_property, rgb_scale=1.0, alpha_scale=1.0, scroll=(0.0, 0.0), st_=None, opacity=1.0):
        color = list(m.colors.get(color_property) or [m.shader.defaults.get(color_property, 0.5)] * 4)
        return Look(blend=blend, texture=main['tex'], st=list(st_ or st), color=color, color_property=color_property,
                    rgb_scale=rgb_scale, alpha_scale=alpha_scale, scroll=list(scroll), cull=cull, queue=m.queue,
                    approximated=approximated, opacity=opacity)

    def check_flow():
        """Refuse a flow distortion the site cannot leave out without changing the picture."""
        nonlocal approximated
        flow = flow_distortion(m, name)
        if not flow:
            return
        if not slight_flow(flow):
            raise LayerError(f'flow distortion (up to {flow["uv"]:g} UV, {flow["texels"]:g} texels)')
        approximated = f'flow distortion (up to {flow["uv"]:g} UV, {flow["texels"]:g} texels) drawn without it'

    if name in PLAIN_TINT:
        if 'HG_SPRITE_SHEET' in keywords:
            raise LayerError('sprite sheet (HG_SPRITE_SHEET)')
        return plain('_TintColor')
    if name in DISTURB_CD or name in RAM_CD:
        if '_HG_UV_ROTATION' in keywords:
            raise LayerError('UV rotation (_HG_UV_ROTATION)')
        if '_HGCUSTOMVERTEXSTREAM_ON' in keywords:
            raise LayerError('custom vertex stream (_HGCUSTOMVERTEXSTREAM_ON)')
        check_flow()
        dissolve = dissolve_factor(m)
        if dissolve is None:
            raise LayerError(f'dissolve (amount {m.float("_Amount", 0.5):g})')
        if name in RAM_CD and m.texture('_RamTex'):
            raise LayerError('ramp texture')
        return plain('_MainColor', alpha_scale=dissolve, opacity=m.float('_Opacity', 1.0), scroll=(m.float('_MainUSpeed'), m.float('_MainVSpeed')))
    if name in ANCHOR:
        check_flow()
        # Without a distortion texture the anchor only shifts the UVs by a constant (the texture reads 0);
        # with one (a slight flow drawn without its wobble) the texture sits at the wobble's centre (0.5).
        offset = [0.0, 0.0]
        for (texture, iu, iv, toggle), (au, av) in zip(ANCHOR_DISTORTIONS, (('_AnchorU', '_AnchorV'), ('_AnchorU_02', '_AnchorV_02'))):
            if toggle and not m.float(toggle):
                continue
            centre = 0.5 if anchor_distorts(m, texture) else 0.0
            offset[0] += (centre - m.float(au, 0.5)) * m.float(iu)
            offset[1] += (centre - m.float(av, 0.5)) * m.float(iv)
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
    images in index order (as shipped: layers' textures, effectTextures, then the particles' own) with how
    their alpha measured, counts for the run log, and layerParticles.json (scripts/particles.py) when the
    export was asked for particles and drew any (layers.json's `particles` then points at it)."""
    document: dict
    textures: list = field(default_factory=list)
    texture_info: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    particles: dict | None = None


def _component_trees(scene: ec._Scene, go: int):
    return [(kind, path_id, scene.read(path_id)[1]) for kind, path_id in scene.components(go)]


def determinant(m) -> float:
    """The determinant of a 3x4 matrix's 3x3 part."""
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def common_projection(timelines: list, points: list, tolerance: float):
    """(L0, factors) when every frame's 2x3 part L factors through the first frame's L0 as L = A L0 (one
    flattening of the mesh holds throughout; factors: each timeline's A per frame, 2x2), or None when
    the mesh turns in depth so that none does. Frames are [t, a, b, c, d, ..., e, f], e and f the depth
    column (last); a residual may move no point of `points` (the mesh's vertices) more than `tolerance`
    in the frames' units."""
    f0 = timelines[0][0]
    l0 = [[f0[1], f0[2], f0[-2]], [f0[3], f0[4], f0[-1]]]
    g = [[sum(l0[i][k] * l0[j][k] for k in range(3)) for j in range(2)] for i in range(2)]
    det = g[0][0] * g[1][1] - g[0][1] * g[1][0]
    if abs(det) < 1e-12:
        return None
    gi = [[g[1][1] / det, -g[0][1] / det], [-g[1][0] / det, g[0][0] / det]]
    pinv = [[sum(l0[k][i] * gi[k][j] for k in range(2)) for j in range(2)] for i in range(3)]  # 3x2
    reach = max((math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2) for p in points), default=1.0) or 1.0
    factors = []
    for frames in timelines:
        out = []
        for f in frames:
            l = [[f[1], f[2], f[-2]], [f[3], f[4], f[-1]]]  # noqa: E741
            a = [[sum(l[i][k] * pinv[k][j] for k in range(3)) for j in range(2)] for i in range(2)]
            back = [[sum(a[i][k] * l0[k][j] for k in range(2)) for j in range(3)] for i in range(2)]
            if max(abs(back[i][j] - l[i][j]) for i in range(2) for j in range(3)) * reach > tolerance:
                return None
            out.append(a)
        factors.append(out)
    return l0, factors


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
    def __init__(self, root_go: int, read, *, mesh_of, texture_of, classify_texture, external_of, shaders, slots, shared=None, particles: bool):
        self.read = read
        # CAB name -> (read, texture_of) of a shared texture bundle (refs/fx/texture/...), or None when it
        # is not available: textures materials take from other bundles.
        self.shared = shared or (lambda cab: None)
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
        self.textures = {}  # (CAB or None, texture path id) -> index
        self.texture_images = []
        self.texture_info = []
        self.omitted = {'particles': 0, 'trails': 0, 'skinned': 0, 'hidden': 0, 'holders': len(self.controller.get('_holders') or []),
                        'custom': [], 'externalTexture': [], 'other': []}
        self.counts = {'static': 0, 'animated': 0, 'follow': 0, 'only': 0, 'states': 0, 'scroll': 0}
        self.masks = []
        # The ParticleSystems as data (scripts/particles.py), when asked for; without it layers.json is what it
        # was before particles were exported (each renderer only counted in omitted.particles).
        self.particles = None
        if particles:
            import particles as particle_export  # here: it builds on this module
            self.particles = particle_export.Particles(self)

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

    def renderer_tree(self, tr, renderer_kind: RendererKind) -> dict:
        """The transform's renderer of that kind (a MeshRenderer, or a ParticleSystem's renderer), or {}."""
        return next((tree for kind, _, tree in _component_trees(self.scene, self.scene.go_of[tr]) if kind == renderer_kind.component), {})

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

    def static_active(self, tr, renderer_kind: RendererKind) -> bool:
        """Whether the renderer of that kind is enabled and every GameObject below its action group is on."""
        if not self.renderer_tree(tr, renderer_kind).get('m_Enabled', 1):
            return False
        return all(self.scene.tree(self.scene.go_of[link], 'GameObject').get('m_IsActive', 1) for link in self.switched_links(tr))

    def toggled(self, tr, renderer_kind: RendererKind) -> bool:
        """Whether a clip (of the default states, or of a state the controller triggers) switches this
        renderer (of that kind) or a GameObject on its chain."""
        for scene in (self.scene, *self.state_scenes.values()):
            if any(scene.float_curves.get((link, ec.GAMEOBJECT, ec.IS_ACTIVE)) for link in self.chain(tr)) or \
                    scene.float_curves.get((tr, renderer_kind.binding, ec.ENABLED)):
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
                renderer = self.renderer_tree(tr, MESH)
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

    def scripts(self, tr, *, ignore: frozenset):
        """(UV scroll [u, v] in Unity UV units per second or None, delay in seconds, {texture property:
        [u, v]} the script scrolls besides the main texture) from the scripts on the layer and its chain;
        LayerError for a script whose effect is not known. `ignore` (required): the field sets of scripts
        known to change nothing this draw shows (particles.IGNORED_SCRIPTS; layers pass none)."""
        scroll, delay, maps = None, 0.0, {}
        for x in self.chain(tr):
            if x == self.root:
                continue
            for kind, _, tree in _component_trees(self.scene, self.scene.go_of[x]):
                if kind != 'MonoBehaviour' or not tree or not tree.get('m_Enabled', 1):
                    continue
                fields = {k for k in tree if not k.startswith('m_')}
                if 'boneName' in fields or frozenset(fields) in ignore:
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
                    # Its second map and extra maps (dissolve and noise textures) move the same way, which
                    # matters only to an effect that samples them (entry checks those it uses).
                    note = {'restarts': not tree.get('keepInitOffset', 1)}
                    second = [float(tree.get('secondXSpeed') or 0.0), float(tree.get('secondYSpeed') or 0.0)]
                    if tree.get('useSecondMap') and not tree.get('protectSecondUV', 0) and any(second) and isinstance(tree.get('secondMapName'), str):
                        maps[tree['secondMapName']] = {**note, 'speed': second, 'tiling': False}
                    for extra in tree.get('extraMapSettings') or []:
                        if not isinstance(extra, dict) or not extra.get('enabled') or not isinstance(extra.get('mapName'), str):
                            continue
                        st = extra.get('mapST') or {}
                        speed2 = [float(extra.get('XSpeed') or 0.0), float(extra.get('YSpeed') or 0.0)]
                        tiling = any(float(st.get(k) or 0.0) for k in ('x', 'y', 'z', 'w'))
                        if any(speed2) or tiling:
                            maps[extra['mapName']] = {**note, 'speed': speed2, 'tiling': tiling}
                    continue
                if '_rotateTex1' in fields:
                    raise LayerError('UV rotation script')
                raise LayerError('script (' + ', '.join(sorted(fields)[:4]) + ')')
        return scroll, delay, maps

    def animated_properties(self, tr, material: Material, renderer_kind: RendererKind) -> set:
        """The material properties a clip (of the default states or a triggered one) drives on this renderer."""
        out = set()
        for scene in (self.scene, *self.state_scenes.values()):
            for (target, type_id, attribute) in scene.float_curves:
                if target == tr and type_id == renderer_kind.binding and attribute != ec.ENABLED:
                    out.add(property_name(material, ec.material_binding(attribute)[0]))
        return out

    def inert(self, material: Material, look_: Look, attribute: int) -> bool:
        """Whether an animated material property changes nothing the game draws: one the shader does not
        declare, the tiling of a texture the material does not bind (Unity's uniform default), or one the
        effect does not use (effects.inert)."""
        h = ec.material_binding(attribute)[0]
        if not declared(material, h):
            return True
        name = property_name(material, h)
        if name.endswith('_ST') and material.texture(name[:-3]) is None:
            return True
        kind = attribute >> 28
        return effects.inert(look_.effect, name, kind % 4 if kind < FLOAT_BINDING else None)

    def check_curves(self, scene, tr, look_: Look, follower, material: Material, renderer_kind: RendererKind):
        """Every curve that touches this layer must be one the export reproduces (or one that changes nothing)."""
        colour_hash = ec.crc(look_.color_property) & 0x0FFFFFFF
        known = {colour_hash, ec.crc('_MainTex_ST') & 0x0FFFFFFF}
        if look_.color_property == '_MainColor':
            known.add(ec.crc('_Opacity') & 0x0FFFFFFF)
        if look_.effect:
            # An effect's animated parameters ride on its frames (extra_values).
            known |= {ec.crc(prop) & 0x0FFFFFFF for path in look_.effect.get('animated_paths') or []
                      for prop in effects.property_names(*look_.effect['params'][path])}
        for (target, type_id, attribute) in scene.float_curves:
            if target != tr:
                continue
            if (type_id == ec.GAMEOBJECT and attribute == ec.IS_ACTIVE) or (type_id == renderer_kind.binding and attribute == ec.ENABLED):
                continue
            if type_id == renderer_kind.binding and (ec.material_binding(attribute)[0] in known or self.inert(material, look_, attribute)):
                continue
            if type_id == renderer_kind.binding:
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

    def clips_of(self, scene, tr, follower, renderer_kind: RendererKind) -> list:
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
            if target == tr and type_id == renderer_kind.binding:
                note(entries)
        return list(clips.values())

    def material_value(self, scene, tr, prop: str, component: int | None, t: float, default: float, renderer_kind: RendererKind) -> float:
        """A material property (a float, or one component of a colour or vector) on tr's renderer at time t,
        as the scene's clips drive it; `default` when none does. Notes the curve as read (check_read)."""
        for key in binding_keys(prop, component):
            if scene.float_curves.get((tr, renderer_kind.binding, key)):
                self.read_curves.add(key)
                return scene.float_value(tr, renderer_kind.binding, key, t, default)
        return default

    def check_read(self, tr, material: Material, look_: Look, renderer_kind: RendererKind):
        """Every material curve on the layer that check_curves let through was read by the sampling: a
        curve read under the wrong binding would otherwise draw its static value without a word."""
        for scene in (self.scene, *self.state_scenes.values()):
            for (target, type_id, attribute) in scene.float_curves:
                if target != tr or type_id != renderer_kind.binding or attribute == ec.ENABLED or attribute in self.read_curves:
                    continue
                if not self.inert(material, look_, attribute):
                    raise LayerError(f'animated material property {property_name(material, ec.material_binding(attribute)[0])} '
                                     f'(binding {attribute >> 28}) is not read')

    def static_value(self, material: Material, prop: str, component: int | None) -> float:
        """A property's value in the material (a float, or one component of a colour, vector or _ST)."""
        if component is None:
            return material.float(prop)
        value = material.colors.get(prop)
        if value:
            return float(value[component])
        if prop.endswith('_ST'):
            env = material.textures.get(prop[:-3]) or {}
            return float([*(env.get('scale') or [1.0, 1.0]), *(env.get('offset') or [0.0, 0.0])][component])
        return float(material.shader.defaults.get(prop, 0.0)) if material.shader and component == 0 else 0.0

    def integrals(self, scene, tr, look_: Look, material: Material, times: list, renderer_kind: RendererKind) -> dict:
        """For each animated `offset` parameter, [u, v, speed u, speed v] at each time: the integral from 0
        of the speed its properties give (Simpson's rule, 8 steps a frame; the curves are cubic between
        keys), and the speed itself, with which a reader carries the offset on past the timeline's end."""
        out = {}
        for path in (look_.effect or {}).get('animated_paths') or []:
            kind, props = look_.effect['params'][path]
            if kind != 'offset':
                continue

            def speed(t, props=props):
                return [scale * self.material_value(scene, tr, prop, component, t, self.static_value(material, prop, component), renderer_kind)
                        for prop, component, scale in props]
            total = [0.0, 0.0]
            values = [[0.0, 0.0, *speed(times[0])]]
            for a, b in zip(times, times[1:]):
                steps = 8
                h = (b - a) / steps
                acc = [0.0, 0.0]
                for k in range(steps + 1):
                    w = 1 if k in (0, steps) else (4 if k % 2 else 2)
                    v = speed(a + k * h)
                    acc = [acc[0] + w * v[0], acc[1] + w * v[1]]
                total = [total[0] + acc[0] * h / 3, total[1] + acc[1] * h / 3]
                values.append([*total, *v])
            out[path] = values
        return out

    def sample(self, scene, tr, look_: Look, follower, material: Material, renderer_kind: RendererKind, static: bool = False):
        """Frames [t, a, b, c, d, tx, ty, r, g, b, alpha, active, su, ou, sv, ov, *parameters, e, f] (the
        matrix to skeleton units, or to the follower's units under a bone follower, with e and f its depth
        column, which entry() folds in or moves; colour with the x2 tint gain; active 0/1; the UV map on the
        exported UVs; the effect's animated parameters) at FPS over the clips that drive the layer, and the
        timeline: (frames, length, loop, loopFrom, animated). Only t = 0 when nothing animates it or
        `static`. Sets self.tilted (the depth column reaches the screen) and self.dets (the signs of the
        frames' 3x3 determinants: Unity culls the other faces of a mirrored object)."""
        self.check_curves(scene, tr, look_, follower, material, renderer_kind)
        clips = [] if static else self.clips_of(scene, tr, follower, renderer_kind)
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
        links = self.chain(tr)
        switched = self.switched_links(tr)
        below = links[links.index(follower[0]) + 1:] if follower else None
        renderer = self.renderer_tree(tr, renderer_kind)
        st0 = look_.st
        opacity_animated = look_.color_property == '_MainColor' and any(scene.float_curves.get((tr, renderer_kind.binding, k)) for k in binding_keys('_Opacity'))
        offsets = self.integrals(scene, tr, look_, material, times, renderer_kind)
        frames = []
        dets = set()
        tilted = False  # depth reaches the screen: a rotation out of the plane
        for index, t in enumerate(times):
            if below is not None:
                m = ec.IDENTITY
                for link in below:
                    m = ec.multiply(m, scene.local(link, t))
                factor = 1.0
            else:
                m = self.relative(scene, tr, t)
                factor = 1.0 / self.unit
            colour = [self.material_value(scene, tr, look_.color_property, c, t, look_.color[c], renderer_kind) for c in range(4)]
            opacity = self.material_value(scene, tr, '_Opacity', None, t, look_.opacity, renderer_kind) if opacity_animated else look_.opacity
            st = [self.material_value(scene, tr, '_MainTex_ST', c, t, st0[c], renderer_kind) for c in range(4)]
            active = 1.0
            for link in switched:
                static_flag = 1.0 if self.scene.tree(self.scene.go_of[link], 'GameObject').get('m_IsActive', 1) else 0.0
                if scene.float_value(link, ec.GAMEOBJECT, ec.IS_ACTIVE, t, static_flag) < 0.5:
                    active = 0.0
            if scene.float_value(tr, renderer_kind.binding, ec.ENABLED, t, 1.0 if renderer.get('m_Enabled', 1) else 0.0) < 0.5:
                active = 0.0
            # The exported UVs carry the material's own ST with v flipped; an animated ST maps them on:
            # u' = (u - o0) s / s0 + o and, in image space, v' = 1 - ((1 - v - o0v) sv / s0v + ov).
            su = st[0] / st0[0] if st0[0] else 1.0
            sv = st[1] / st0[1] if st0[1] else 1.0
            if abs(m[0][2]) > 1e-6 or abs(m[1][2]) > 1e-6:
                tilted = True
            det = determinant(m)
            if abs(det) > 1e-12:
                dets.add(det > 0)
            frames.append([t, m[0][0] * factor, m[0][1] * factor, m[1][0] * factor, m[1][1] * factor, m[0][3] * factor, m[1][3] * factor,
                           2 * colour[0] * look_.rgb_scale, 2 * colour[1] * look_.rgb_scale, 2 * colour[2] * look_.rgb_scale,
                           2 * colour[3] * look_.alpha_scale * opacity, active,
                           su, st[2] - st0[2] * su, sv, 1 - sv - st[3] + st0[3] * sv, *self.extra_values(scene, tr, look_, material, t, offsets, index, renderer_kind),
                           m[0][2] * factor, m[1][2] * factor])
        animated = len(frames) > 1 and any(f[1:] != frames[0][1:] for f in frames)
        self.tilted = tilted
        self.dets = dets
        return frames, round(length, 4), bool(looping), round(loop_from, 4), animated

    def extra_values(self, scene, tr, look_: Look, material: Material, t: float, offsets: dict, index: int, renderer_kind: RendererKind) -> list:
        """An effect's animated parameters at time t, in the order of its animated_paths (effects.width
        numbers each): float properties by name, vector and colour ones by component, offsets integrated."""
        if not look_.effect or not look_.effect.get('animated_paths'):
            return []
        out = []
        for path in look_.effect['animated_paths']:
            kind, props = look_.effect['params'][path]
            if kind == 'offset':
                out += offsets[path][index]
            elif kind == 'float':
                out += [self.material_value(scene, tr, prop, None, t, material.float(prop), renderer_kind) for prop in props]
            else:
                prop = props[0]
                out += [self.material_value(scene, tr, prop, c, t, self.static_value(material, prop, c), renderer_kind) for c in range(effects.width(path))]
        return out

    def decimate(self, frames, extent: float, to_skeleton: float, depth: bool = False) -> list:
        """Drops frames a straight line reproduces, with tolerances from the layer's size so no vertex
        strays more than POSITION_TOLERANCE skeleton units. `depth`: the frames carry the depth column
        (e, f) at 16 and 17, before the parameters."""
        matrix_tol = POSITION_TOLERANCE / max(extent * to_skeleton, 1e-6)
        move_tol = POSITION_TOLERANCE / to_skeleton
        tolerances = [matrix_tol] * 4 + [move_tol] * 2 + [COLOUR_TOLERANCE] * 4 + [0.01] + [UV_TOLERANCE] * 4 + ([matrix_tol] * 2 if depth else [])
        tolerances += [EFFECT_TOLERANCE] * (len(frames[0]) - 1 - len(tolerances))
        kept = ec.decimate(frames, tolerances)
        return [[round(v, 6) if i in (1, 2, 3, 4, 12, 13, 14, 15) or i > 15 else round(v, 4) for i, v in enumerate(f)] for f in kept]

    def texture_source(self, texture: dict):
        """(read, texture_of) for a texture reference: this bundle's, or the shared bundle holding it;
        LayerError when that bundle is not available."""
        if 'external' not in texture:
            return self.read, self.texture_of
        source = self.shared(texture['external'])
        if source is None or texture.get('id') is None:
            raise LayerError(f'texture in {texture["external"]}')
        entry = source[0](texture['id'])
        if not entry or entry[0] != 'Texture2D':
            raise LayerError(f'texture {texture["id"]} not in {texture["external"]}')
        return source[0], source[1]

    def texture_index(self, texture: dict, main: bool = True) -> int:
        """The texture's index (in the order first used), its image written once. A main texture must show
        something; an effect's map (noise, dissolve, weight, ramp) is read whatever its alpha."""
        key = (texture.get('external'), texture['id'])
        if key in self.textures:
            return self.textures[key]
        read, texture_of = self.texture_source(texture)
        tid = texture['id']
        tree = read(tid)[1]
        image = texture_of(tid)
        alpha = texture_alpha(image, self.classify_texture)
        settings = tree.get('m_TextureSettings') or {}
        modes = ['repeat', 'clamp', 'mirror', 'mirror-once']
        wrap_u = settings.get('m_WrapU', 0)
        wrap_v = settings.get('m_WrapV', wrap_u)
        wrap = [modes[w] if isinstance(w, int) and 0 <= w < 4 else 'repeat' for w in (wrap_u, wrap_v)]
        if 'mirror-once' in wrap:
            raise LayerError('texture wraps mirror-once')
        box = image.getchannel('A').point(lambda a: 255 if a >= OPAQUE_ALPHA else 0).getbbox()
        if box is None and main:
            raise LayerError('nothing visible (its texture is transparent)')
        box = box or (0, 0, image.width, image.height)
        # The part of the texture that shows, in image-space UV: the frame is fitted to it, not to the
        # transparent margin around it.
        opaque = [round(box[0] / image.width, 5), round(box[1] / image.height, 5), round(box[2] / image.width, 5), round(box[3] / image.height, 5)]
        index = len(self.texture_images)
        self.textures[key] = index
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
        renderer = self.renderer_tree(tr, MESH)
        materials = [r for r in renderer.get('m_Materials') or [] if isinstance(r, dict)]
        if not materials:
            raise LayerError('no material')
        if renderer.get('m_SortingLayerID', 0) != 0:
            raise LayerError(f'sorting layer {renderer.get("m_SortingLayerID")}')
        only, _ = self.group(tr)
        scroll_script, delay, map_scrolls = self.scripts(tr, ignore=frozenset())
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
            try:
                material = read_material(self.read(ref['m_PathID'])[1], read=self.read, external_of=self.external_of, shaders=self.shaders, home=None)
                look_ = look(material, self.animated_properties(tr, material, MESH))
            except LayerError as error:
                self.omit('custom', label, str(error))
                continue
            except UNREADABLE as error:
                self.omit('other', label, unreadable(error))
                continue
            try:
                out.append(self.entry(tr, label, look_, material, triangles, mesh, follower, only, delay, scroll_script, map_scrolls))
            except LayerError as error:
                if str(error) == 'hidden':
                    self.omitted['hidden'] += 1
                else:
                    self.omit('externalTexture' if str(error).startswith('texture in ') else 'other', label, str(error))
            except ec.CameraError as error:
                self.omit('other', label, str(error))
            except UNREADABLE as error:
                self.omit('other', label, unreadable(error))
        return out

    def follow_of(self, follower) -> tuple[dict, float, float, bool]:
        """model `follow` for a BoneFollower, how many skeleton units one of its units is, its z, and whether
        its parent's depth axis is mirrored (a negative z scale: Unity culls the other faces, and `parent`
        does not show it)."""
        ftr, fields = follower
        parent = self.scene.parent[ftr]
        immediate = parent == self.root
        root_q = self.scene.rotation(self.root, 0.0)
        inv_root_q = (-root_q[0], -root_q[1], -root_q[2], root_q[3])
        if immediate:
            # spine-unity's local path: the follower's local position and rotation are the bone's.
            k = [[1.0, 0.0], [0.0, 1.0]]
            mirrored = False
            depth_mirrored = False
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
            depth_mirrored = pr[2][2] < 0
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
        return follow, _matrix_scale([[v / self.unit for v in row] for row in k]), own[2][3], depth_mirrored

    def entry(self, tr, label, look_: Look, material: Material, triangles, mesh, follower, only, delay, scroll_script, map_scrolls=None) -> dict:
        if look_.blend not in ('alpha', 'add'):
            raise LayerError(f'blend {look_.blend}')
        if look_.texture is None and not look_.effect:
            raise LayerError('no main texture')
        if look_.texture is not None:
            self.texture_source(look_.texture)  # a texture in another bundle needs that bundle
        if not triangles or len(triangles) % 3:
            raise LayerError('no triangles')
        if scroll_script and any(look_.scroll):
            raise LayerError('UV scroll from both its shader and a script')
        self.read_curves = set()
        flat = all(abs(v[2]) <= 1e-6 for v in mesh['vertices'])
        frames, length, loop, loop_from, animated = self.sample(self.scene, tr, look_, follower, material, MESH)
        tilted, dets = self.tilted, set(self.dets)
        if not animated:
            frames, length, loop, loop_from, _ = self.sample(self.scene, tr, look_, follower, material, MESH, static=True)
        # The states the controller's triggers put its _animators in (Interact, Special...), where they
        # play something else for this layer.
        states = {}
        for animation_name, scene in self.state_scenes.items():
            other = self.sample(scene, tr, look_, follower, material, MESH)
            tilted = tilted or self.tilted
            dets |= self.dets
            if other[0] != frames and (other[4] or other[0][0][1:] != frames[0][1:]):
                states[animation_name] = other
        self.check_read(tr, material, look_, MESH)
        first = frames[0]
        if not animated and first[11] < 0.5 and not any(any(f[11] >= 0.5 for f in st[0]) for st in states.values()):
            raise LayerError('hidden')
        animated = animated or bool(states)
        moving = animated or follower is not None
        follow, to_skeleton, z, depth_mirrored = None, 1.0, 0.0, False
        if follower is not None:
            follow, to_skeleton, z, depth_mirrored = self.follow_of(follower)
        # Unity culls the other faces of an object whose transform mirrors it (a negative determinant): the
        # faces kept are those that wind clockwise on screen, or anticlockwise when mirrored.
        cull = look_.cull
        if cull and len(dets) > 1:
            raise LayerError('a culled layer turned inside out while it moves')
        if cull and ((dets == {False}) != depth_mirrored):
            cull = 3 - cull
        used = sorted(set(triangles))
        local = [mesh['vertices'][v] for v in used]
        # How the mesh's depth reaches the screen (the camera is orthographic): `flat`, it does not (the
        # frames' 2x3 places the mesh's x and y); `projected`, one flattening holds throughout (the mesh
        # is flattened by it and the frames place the result); `solid`, it turns in depth, so its 3D
        # vertices and a 2x4 projection per frame go to a `tilted` entry.
        route = 'flat'
        vertex_depth = bool(look_.effect and look_.effect.get('family') == effects.PARTICLE and look_.effect.get('vertex'))
        if animated and tilted and (not flat or vertex_depth):
            common = common_projection([frames] + [st[0] for st in states.values()], local, PROJECTION_TOLERANCE / to_skeleton)
            route = 'projected' if common else 'solid'
        if moving and (cull or route == 'solid') and not look_.effect:
            # A layer that culls faces while it moves is culled as it is drawn (its `cull`), and a solid one
            # needs its own projection: both only an effect entry carries. Drawn as one, with its exact effect
            # or with none.
            if look_.exact:
                look_ = replace(look_, effect={k: v for k, v in look_.exact.items() if k != 'st'}, st=list(look_.exact['st']), approximated=None, exact=None)
            else:
                look_ = replace(look_, effect=plain_effect(look_), approximated=None)
        # An effect's unbound main texture is Unity's default white (null).
        texture = self.texture_index(look_.texture) if look_.texture is not None else None
        remap = {v: i for i, v in enumerate(used)}
        tris = [remap[i] for i in triangles]
        st = look_.st
        uvs = []
        for v in used:
            u0, v0 = mesh['uv'][v][:2]
            uvs += [u0 * st[0] + st[2], 1 - (v0 * st[1] + st[3])]
        colors = [round(float(c), 4) for v in used for c in mesh['colors'][v][:4]] if mesh.get('colors') else None
        # An effect samples in Unity's UV space from the mesh's own UVs (raw_uvs) with the main tiling
        # (main_st) as a parameter: its noise, dissolve and ramp maps have tilings of their own.
        raw_uvs = [round(float(c), 6) for v in used for c in mesh['uv'][v][:2]] if (look_.effect or look_.exact) else None
        exact_st = list(look_.exact['st']) if look_.exact else None
        main_st = list(st)
        colour = None
        if not animated:
            colour = [round(c, 4) for c in first[7:11]]
            uvs = [uvs[i] * first[12] + first[13] if i % 2 == 0 else uvs[i] * first[14] + first[15] for i in range(len(uvs))]
            # The same UV map on the tiling, in Unity space (image v = 1 - Unity v).
            su, ou, sv, ov = first[12:16]
            bake = lambda t: [t[0] * su, t[1] * sv, t[2] * su + ou, t[3] * sv + 1 - sv - ov]  # noqa: E731
            main_st = bake(st)
            exact_st = bake(exact_st) if exact_st else None
        uvs = [round(x, 6) for x in uvs]
        if follower is not None:
            self.counts['follow'] += 1
        # Culled as drawn: a moving layer's faces turn (a static one's are culled here, below).
        drawn_cull = cull if moving else 0
        animation = None
        if animated:
            # Skeleton units per unit of the frames' matrix target: 1 (skeleton), or the follower's.
            scale = to_skeleton if follower is not None else 1.0
            timelines = [frames] + [st_[0] for _, st_ in sorted(states.items())]
            if route == 'projected':
                l0, factors = common
                factor_of = dict(zip([id(t) for t in [frames] + [st_[0] for st_ in states.values()]], factors))
                points = [(l0[0][0] * p[0] + l0[0][1] * p[1] + l0[0][2] * p[2], l0[1][0] * p[0] + l0[1][1] * p[1] + l0[1][2] * p[2]) for p in local]
                vertices = [round(c, 5) for p in points for c in p]
                extent = max((math.hypot(*p) for p in points), default=1.0) or 1.0
                vertex_matrix = [l0[0][0], l0[0][1], l0[0][2], l0[1][0], l0[1][1], l0[1][2]]
                timelines = [[[f[0], a[0][0], a[0][1], a[1][0], a[1][1], *f[5:-2]] for f, a in zip(t, factor_of[id(t)])] for t in timelines]
            elif route == 'solid':
                vertices = [round(c, 5) for p in local for c in p[:3]]
                extent = max((math.sqrt(p[0] ** 2 + p[1] ** 2 + p[2] ** 2) for p in local), default=1.0) or 1.0
                vertex_matrix = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
                timelines = [[[*f[:16], *f[-2:], *f[16:-2]] for f in t] for t in timelines]
            else:
                vertices = [round(c, 5) for p in local for c in p[:2]]
                extent = max((math.hypot(p[0], p[1]) for p in local), default=1.0) or 1.0
                vertex_matrix = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
                timelines = [[f[:-2] for f in t] for t in timelines]
            depth = route == 'solid'
            animation = {'length': length, 'loop': loop, 'loopFrom': loop_from, 'frames': self.decimate(timelines[0], extent, scale, depth)}
            if states:
                animation['states'] = {name_: {'length': st_[1], 'loop': st_[2], 'loopFrom': st_[3], 'frames': self.decimate(line, extent, scale, depth)}
                                       for (name_, st_), line in zip(sorted(states.items()), timelines[1:])}
            if follower is None:
                z = self.relative(self.scene, tr, 0.0)[2][3]
        elif follower is not None:
            links = self.chain(tr)
            below = ec.IDENTITY
            for link in links[links.index(follower[0]) + 1:]:
                below = ec.multiply(below, self.scene.local(link, 0.0))
            # The orthographic camera drops depth: the mesh is flattened through its matrix below the follower.
            vertices = [round(c, 5) for v in used for c in ec.transform_point(below, mesh['vertices'][v])[:2]]
            vertex_matrix = [below[0][0], below[0][1], below[0][2], below[1][0], below[1][1], below[1][2]]
        else:
            m = self.relative(self.scene, tr, 0.0)
            points = [ec.transform_point(m, mesh['vertices'][v]) for v in used]
            if cull:
                kept = []
                for i in range(0, len(tris), 3):
                    a, b, c = (points[tris[i + j]] for j in range(3))
                    area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                    # Unity's front faces wind clockwise on screen: Cull Back keeps the clockwise ones.
                    if (cull == 2 and area <= 0) or (cull == 1 and area >= 0):
                        kept += tris[i:i + 3]
                if not kept:
                    raise LayerError('every face culled')
                tris = kept
            vertices = [round(p[k] / self.unit, 3) for p in points for k in (0, 1)]
            z = sum(p[2] for p in points) / len(points)
            vertex_matrix = [m[0][0] / self.unit, m[0][1] / self.unit, m[0][2] / self.unit, m[1][0] / self.unit, m[1][1] / self.unit, m[1][2] / self.unit]
        speed = scroll_script or (look_.scroll if any(look_.scroll) else None)
        scroll = [round(speed[0], 6), round(-speed[1], 6)] if speed else None  # image space flips v
        renderer = self.renderer_tree(tr, MESH)
        sort = (renderer.get('m_SortingLayerID', 0), renderer.get('m_SortingOrder', 0), look_.queue, -z, self.walk[tr])
        if look_.effect:
            # Animated parameters: the frames carry them past column 16 (18 for a tilted entry); a static
            # layer keeps their first values.
            paths = look_.effect.get('animated_paths') or []
            shader = self.shader_json(look_.effect, main_st, look_.scroll, scroll_script, map_scrolls or {}, vertex_matrix,
                                      values=first[16:16 + sum(effects.width(p) for p in paths)], animated=bool(animation))
            layer = {'name': label, 'blend': look_.blend, 'texture': texture, 'color': colour, 'vertices': vertices, 'uvs': raw_uvs, 'colors': colors,
                     'triangles': tris, 'follow': follow, 'animation': animation, 'only': only, 'delay': round(delay, 4), 'cull': drawn_cull, 'shader': shader}
            layer['visible'] = self.visible_box(layer, first)
            return {'tilted' if route == 'solid' else 'effect': layer, 'sort': sort}
        layer = {'name': label, 'blend': look_.blend, 'texture': texture, 'color': colour, 'vertices': vertices, 'uvs': uvs, 'colors': colors,
                 'triangles': tris, 'follow': follow, 'animation': animation, 'scroll': scroll, 'only': only, 'delay': round(delay, 4),
                 'approximated': look_.approximated}
        if look_.approximated:
            if look_.exact:
                # Readers that know the effect draw it exactly; others draw the approximation.
                try:
                    layer['exact'] = {'uvs': raw_uvs, 'shader': self.shader_json(look_.exact, exact_st, look_.exact['main']['speed'], scroll_script,
                                                                                   map_scrolls or {}, vertex_matrix)}
                except LayerError as error:
                    layer['approximated'] += f' (exactly: {error})'
        return {'layer': layer, 'sort': sort}

    def visible_box(self, layer: dict, first: list):
        """Where an effect shows at its first frame, in the mesh's own UVs ([u0, v0, u1, v1]), or None when it
        shows in under VISIBLE_COVERAGE of them (scattered sparks and stars, or nothing yet): its main
        texture's alpha (an additive one's colour x alpha) x its colour x its dissolves, sampled over the
        mesh's UVs, undistorted. The frame the site opens on fits it (effectBounds): an opaque glow texture
        faded down, a sky dissolved to wisps or a field of stars does not stretch it."""
        shader = layer['shader']
        colour = first[7:11]
        add = layer['blend'] == 'add'
        uvs = layer['uvs']
        us, vs = uvs[0::2], uvs[1::2]
        u_lo, u_hi, v_lo, v_hi = min(us), max(us), min(vs), max(vs)
        main = shader['main']['st']
        su, ou, sv, ov = first[12:16]
        st = [main[0] * su, main[1] * sv, main[2] * su + ou, main[3] * sv + 1 - sv - ov] if layer['animation'] else main

        def sampler(index):
            if index is None:
                return lambda u, v: (255, 255, 255, 255)
            image = self.texture_images[index]
            wrap = self.texture_info[index]['wrap']
            pixels = image.load()
            w, h = image.size

            def fold(x, mode):
                if mode == 'clamp':
                    return min(max(x, 0.0), 1.0)
                if mode == 'mirror':
                    x = x % 2.0
                    return 2.0 - x if x > 1.0 else x
                return x % 1.0

            def sample(u, v):
                x = fold(u, wrap[0])
                y = fold(1.0 - v, wrap[1])  # Unity v up, image rows down
                return pixels[min(int(x * w), w - 1), min(int(y * h), h - 1)]
            return sample
        main_tex = sampler(layer['texture'])
        if shader['family'] == effects.NOISE:
            dissolves = []
        else:
            dissolves = [(sampler(d['texture']), d) for d in shader['dissolve']]
        found = []
        steps = 48
        for i in range(steps + 1):
            u = u_lo + (u_hi - u_lo) * i / steps
            for j in range(steps + 1):
                v = v_lo + (v_hi - v_lo) * j / steps
                mu, mv = u * st[0] + st[2], v * st[1] + st[3]
                r, g, b, a = main_tex(mu, mv)
                alpha = a / 255 * colour[3]
                for sample, d in dissolves:
                    du, dv = u * d['st'][0] + d['st'][2], v * d['st'][1] + d['st'][3]
                    f = (sample(du, dv)[0] / 255 - d['amount'] + d['border'] * effects.dissolve_k(d['amount'])) / d['border']
                    alpha *= min(max(f, 0.0), 1.0)
                alpha = min(alpha, 1.0)
                if add:
                    alpha *= min(max(r / 255 * colour[0], g / 255 * colour[1], b / 255 * colour[2]), 1.0)
                if alpha * 255 >= OPAQUE_ALPHA:
                    found.append((u, v))
        if len(found) < VISIBLE_COVERAGE * (steps + 1) ** 2:
            return None
        pad_u, pad_v = (u_hi - u_lo) / steps, (v_hi - v_lo) / steps
        return [round(min(p[0] for p in found) - pad_u, 5), round(min(p[1] for p in found) - pad_v, 5),
                round(max(p[0] for p in found) + pad_u, 5), round(max(p[1] for p in found) + pad_v, 5)]

    def shader_json(self, effect: dict, main_st: list, speed: list, script: list | None, map_scrolls: dict, vertex_matrix: list,
                    values: list = (), animated: bool = False) -> dict:
        """layers.json's `shader` for an effect (effects.describe): its textures as indices, every map with
        its tiling (st), the shader's own scroll (speed, wrapped as the GLSL wraps it) and a UV scroll
        script's (scroll, not wrapped). `values`: the animated parameters at the first frame; `animated`:
        whether the layer's frames carry them (`animated` lists their paths)."""
        r6 = lambda values: [round(float(v), 6) for v in values]  # noqa: E731

        def scroll_of(name: str) -> list:
            note = map_scrolls.get(name)
            if not note:
                return [0.0, 0.0]
            if note['restarts']:
                raise LayerError('UV scroll script that restarts the offset')
            if note['tiling']:
                raise LayerError('UV scroll script that sets a tiling')
            return r6(note['speed'])

        def texture_map(ref: dict, **extra) -> dict:
            index = self.texture_index(ref['tex'], main=False) if ref.get('tex') else None
            return {'texture': index, 'st': r6(ref['st']), 'speed': r6(ref.get('speed') or [0.0, 0.0]), 'scroll': scroll_of(ref['name']), **extra}

        main = {'st': r6(main_st), 'speed': r6(speed if not script else [0.0, 0.0]), 'scroll': r6(script or [0.0, 0.0]),
                'fract': bool((effect.get('main') or {}).get('fract'))}
        if effect['family'] == effects.NOISE:
            if not effect.get('noise'):
                raise LayerError('Disturb2 without its noise texture')
            out = {'family': effects.NOISE, 'mode': effect['mode'], 'main': main, 'noise': texture_map(effect['noise']),
                   'noise1': r6(effect['noise1']), 'noise2': r6(effect['noise2']), 'glow': r6(effect['glow']) if effect.get('glow') else None,
                   'animated': []}
            paths = effect.get('animated_paths') or []
            set_values(out, paths, list(values))
            if animated:
                out['animated'] = list(paths)
            return out
        out = {'family': effects.PARTICLE, 'main': main, 'distort': None, 'dissolve': [], 'edge': None, 'ramp': None, 'vertex': None, 'animated': []}
        distort = effect.get('distort')
        if distort:
            out['distort'] = {'space': distort['space'], 'main': round(distort['main'], 6), 'dissolve': round(distort['dissolve'], 6),
                              'constant': r6(distort.get('constant') or [0.0, 0.0]),
                              'maps': [texture_map(m, anchor=r6(m['anchor']), intensity=r6(m['intensity'])) for m in distort['maps']],
                              'weight': texture_map(distort['weight']) if distort.get('weight') else None}
        for d in effect.get('dissolve') or []:
            out['dissolve'].append(texture_map(d, fract=bool(d.get('fract')), amount=round(d['amount'], 6), border=round(d['border'], 6)))
        if effect.get('edge'):
            e = effect['edge']
            out['edge'] = {'color': r6(e['color']), 'pow': round(e['pow'], 6), 'epsilon': bool(e['epsilon'])}
        if effect.get('ramp'):
            out['ramp'] = texture_map(effect['ramp'])
        v = effect.get('vertex')
        if v:
            out['vertex'] = {**texture_map(v), 'intensity': r6(v['intensity']), 'weight': texture_map(v['weight']) if v.get('weight') else None,
                             'matrix': r6(vertex_matrix)}
        paths = effect.get('animated_paths') or []
        set_values(out, paths, list(values))
        if animated:
            out['animated'] = list(paths)
        if not (out['distort'] or out['dissolve'] or out['edge'] or out['ramp'] or out['vertex']) and not any(main['speed']) and not main['fract'] \
                and not effect.get('plain'):
            raise LayerError('an effect that changes nothing')  # cannot happen: look() would have found it plain
        return out

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
                    if self.particles is not None:
                        self.particles.note(tr, kind)
                    else:
                        self.omitted['particles'] += 1
                elif kind in ('TrailRenderer', 'LineRenderer'):
                    self.omitted['trails'] += 1  # layers draw none (the particle export writes TrailRenderers as data)
                    if kind == 'TrailRenderer' and self.particles is not None:
                        self.particles.note(tr, kind)
                elif kind == 'SkinnedMeshRenderer':
                    self.omitted['skinned'] += 1
                elif kind in ('SpriteRenderer', 'BillboardRenderer', 'CanvasRenderer'):
                    self.omit('other', name, kind)
                elif kind == 'MeshRenderer':
                    try:
                        self.note_mask(tr, tree)
                    except UNREADABLE as error:
                        self.omit('other', f'{name} (mask)', unreadable(error))
                    if any(k == 'MonoBehaviour' and t and 'skeletonDataAsset' in t for k, _, t in components):
                        self.omit('other', name, 'nested skeleton')
                    elif not self.static_active(tr, MESH) and not self.toggled(tr, MESH):
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
                        except UNREADABLE as error:
                            self.omit('other', name, unreadable(error))
        if self.particles is not None:
            entries += self.particles.entries()
        entries.sort(key=lambda e: e['sort'])
        entries = self.unmasked(entries)
        # Particle systems that sort next to each other, with no part, layer or effect between them, are one
        # run: {"particles": [system indices]}, the systems numbered in draw order. A system that draws
        # nothing itself (a sub-emitter spawner, or one that draws only trails) has its index and no run. A
        # TrailRenderer (written as data, not drawn yet) notes where it would draw: before draw entry i, or
        # after the first n systems of the particle run at i (`draw`: [i, n]).
        draw, systems, transforms, trails = [], [], [], []
        for e in entries:
            if 'particles' in e:
                if e['particles']['render'] is not None:
                    if draw and 'particles' in draw[-1]:
                        draw[-1]['particles'].append(len(systems))
                    else:
                        draw.append({'particles': [len(systems)]})
                systems.append(e['particles'])
                transforms.append(e['transform'])
                continue
            if 'trailRenderer' in e:
                at = [len(draw) - 1, len(draw[-1]['particles'])] if draw and 'particles' in draw[-1] else [len(draw), 0]
                e['trailRenderer']['draw'] = at
                trails.append(e['trailRenderer'])
                continue
            draw += [{kind: e[kind]} for kind in ('part', 'layer', 'effect', 'tilted') if kind in e]
        for trail in trails:  # after a whole run is before the entry after it
            i, n = trail['draw']
            if n and n == len(draw[i]['particles']):
                trail['draw'] = [i + 1, 0]
        if trails and not systems:
            for trail in trails:
                self.particles.trail_omitted(trail['name'], 'no particle system is exported to carry it')
            trails = []
        # Textures still drawn, renumbered in order: first those plain layers draw (`textures`, which every
        # reader fetches), then those only effects sample (`effectTextures`, which readers that know
        # effects fetch), numbered on from the first, then those only particles sample (layerParticles.json's
        # `textures`, which no reader of layers.json fetches).
        plain = sorted({d['layer']['texture'] for d in draw if 'layer' in d})
        refs = []  # every reference an effect makes: (holder dict, key)
        for d in draw:
            shaded = d.get('effect') or d.get('tilted')
            if shaded:
                refs.append((shaded, 'texture'))  # null: Unity's white
                refs += [(m, 'texture') for m in shader_maps(shaded['shader'])]
            elif 'layer' in d and d['layer'].get('exact'):
                refs += [(m, 'texture') for m in shader_maps(d['layer']['exact']['shader'])]
        only_effects = sorted({holder[key] for holder, key in refs if holder[key] is not None} - set(plain))
        particle_refs = self.particles.texture_refs(systems, trails) if self.particles is not None else []
        only_particles = sorted({holder[key] for holder, key in particle_refs if holder[key] is not None} - set(plain) - set(only_effects))
        order = plain + only_effects + only_particles
        renumber = {old: new for new, old in enumerate(order)}
        for d in draw:
            if 'layer' in d:
                d['layer']['texture'] = renumber[d['layer']['texture']]
        for holder, key in refs + particle_refs:
            if holder[key] is not None:
                holder[key] = renumber[holder[key]]
        self.texture_images = [self.texture_images[i] for i in order]
        self.texture_info = [self.texture_info[i] for i in order]
        records = [{'file': f'layer{i}.webp', 'width': info['width'], 'height': info['height'], 'wrap': info['wrap'], 'opaque': info['opaque']}
                   for i, info in enumerate(self.texture_info)]
        for bucket in ('custom', 'externalTexture', 'other'):
            self.omitted[bucket].sort(key=lambda item: (item['name'], item['reason']))
        layer_textures = len(plain) + len(only_effects)
        document = {'schemaVersion': SCHEMA_VERSION, 'textures': records[:len(plain)], 'effectTextures': records[len(plain):layer_textures], 'bounds': None,
                    'effectBounds': None, 'separators': self.separators, 'draw': draw, 'omitted': self.omitted}
        particle_document = None
        if self.particles is not None:
            # The pointer to layerParticles.json (scripts/sync.py writes it with the file's bytes and sha256),
            # null when no system is drawn; omitted.particles counts the systems left out, each with its reason.
            document = {**{k: document[k] for k in ('schemaVersion', 'textures', 'effectTextures', 'bounds', 'effectBounds')}, 'particles': None,
                        **{k: document[k] for k in ('separators', 'draw', 'omitted')}}
            self.omitted['particles'] = len(self.particles.reasons)
            self.omitted['particleReasons'] = sorted(self.particles.reasons, key=lambda item: (item['name'], item['reason']))
            if systems:
                particle_document = self.particles.document(systems, transforms, trails, records[layer_textures:], layer_textures)
        layers = [d['layer'] for d in draw if 'layer' in d]
        drawn = layers + [d.get('effect') or d['tilted'] for d in draw if 'effect' in d or 'tilted' in d]
        self.counts = {'layers': len(drawn), 'plain': len(layers), 'effects': len(drawn) - len(layers),
                       'tilted': sum(1 for d in draw if 'tilted' in d),
                       'exact': sum(1 for l in layers if l.get('exact')), 'parts': sum(1 for d in draw if 'part' in d),
                       'static': sum(1 for l in drawn if not l['animation'] and not l['follow']),
                       'animated': sum(1 for l in drawn if l['animation']), 'follow': sum(1 for l in drawn if l['follow']),
                       'only': sum(1 for l in drawn if l['only']), 'states': sum(1 for l in drawn if l['animation'] and l['animation'].get('states')),
                       'scroll': sum(1 for l in layers if l['scroll'])}
        if self.particles is not None:
            self.counts['particles'] = len(systems)
            self.counts['particleRuns'] = sum(1 for d in draw if 'particles' in d)
            self.counts['particleTextures'] = len(only_particles)
        return LayerExport(document, self.texture_images, self.texture_info, self.counts, particle_document)

    # --- masks

    def note_mask(self, tr, renderer: dict):
        """Remembers a visible renderer drawn with the Erase mask shader: it paints over what was drawn
        before it (alpha from its texture), so those layers do not look as the site would draw them."""
        names, queues = [], []
        for ref in renderer.get('m_Materials') or []:
            if isinstance(ref, dict) and ref.get('m_FileID', 0) == 0 and ref.get('m_PathID'):
                entry = self.read(ref['m_PathID'])
                if entry and entry[0] == 'Material':
                    material = read_material(entry[1], read=self.read, external_of=self.external_of, shaders=self.shaders, home=None)
                    names.append(material.shader.name if material.shader else '')
                    queues.append(material.queue)
        erase = [q for n, q in zip(names, queues) if '/Mask/Erase' in n]
        if not erase or not (self.static_active(tr, MESH) or self.toggled(tr, MESH)):
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
        m = self.relative(self.scene, tr, 0.0)
        points = [ec.transform_point(m, p) for p in (corners or [(0.0, 0.0, 0.0)])]
        if corners is not None and not self.moves(tr):
            bounds = (min(p[0] for p in points) / self.unit, min(p[1] for p in points) / self.unit,
                      max(p[0] for p in points) / self.unit, max(p[1] for p in points) / self.unit)
        # Ordered exactly as the layers are (sorting layer, order, render queue, depth, hierarchy): the
        # Erase shader is a Transparent-queue material, so depth decides between it and a layer.
        z = sum(p[2] for p in points) / len(points)
        sort = (renderer.get('m_SortingLayerID', 0), renderer.get('m_SortingOrder', 0), erase[0], -z, self.walk[tr])
        self.masks.append({'name': self.name(tr), 'sort': sort, 'bounds': bounds})

    def layer_bounds(self, layer: dict, solid: bool = False):
        """A layer's box in skeleton units over its timeline, or None when a bone places it (unknown here).
        `solid`: a tilted entry (3D vertices, the depth column at 16 and 17 of its frames)."""
        if layer['follow']:
            return None
        v = layer['vertices']
        stride = 3 if solid else 2
        lines = [layer['animation']] + list((layer['animation'] or {}).get('states', {}).values()) if layer['animation'] else []
        frames = [[*f[1:7], *(f[16:18] if solid else (0.0, 0.0))] for line in lines for f in line['frames']] or [[1, 0, 0, 1, 0, 0, 0, 0]]
        xs, ys = [], []
        for a, b, c, d, tx, ty, e, f in frames:
            for i in range(0, len(v), stride):
                depth = v[i + 2] if solid else 0.0
                xs.append(a * v[i] + b * v[i + 1] + e * depth + tx)
                ys.append(c * v[i] + d * v[i + 1] + f * depth + ty)
        return min(xs), min(ys), max(xs), max(ys)

    def unmasked(self, entries: list) -> list:
        """Leaves out the layers an Erase mask drawn after them overlaps: drawn without it they would
        show what the game paints over."""
        if not self.masks:
            return entries
        kept = []
        for entry in entries:
            kind = next((k for k in ('layer', 'effect', 'tilted') if k in entry), None)
            if kind:
                box = self.layer_bounds(entry[kind], kind == 'tilted')
                for mask in self.masks:
                    if mask['sort'] <= entry['sort']:
                        continue  # drawn before the layer: it paints over nothing of it
                    m = mask['bounds']
                    if m is None or box is None or (box[0] < m[2] and m[0] < box[2] and box[1] < m[3] and m[1] < box[3]):
                        self.omit('other', entry[kind]['name'], f'under the mask {mask["name"]} (Erase), which is not drawn')
                        break
                else:
                    kept.append(entry)
                continue
            kept.append(entry)
        return kept


def set_values(shader: dict, paths: list, values: list):
    """Writes animated parameters (paths as effects.parameters names them, effects.width numbers each)
    into a layers.json `shader`."""
    at = 0
    for path in paths:
        n = effects.width(path)
        chunk = [round(float(v), 6) for v in values[at:at + n]]
        at += n
        if len(chunk) < n:
            return
        if path.endswith('.offset'):
            continue  # an integrated speed: 0 at the first frame, and only frames carry it
        *where, key = path.split('.')
        node = shader
        for part in where:
            node = node[int(part)] if part.isdigit() else node[part]
        node[key] = chunk[0] if n == 1 else chunk


def shader_maps(shader: dict) -> list[dict]:
    """The members of a layers.json `shader` that name a texture (`texture` index, or null for an unbound
    noise map that reads 0)."""
    if shader['family'] == effects.NOISE:
        return [shader['noise']]
    out = []
    distort = shader.get('distort')
    if distort:
        out += distort['maps']
        if distort.get('weight'):
            out.append(distort['weight'])
    out += shader.get('dissolve') or []
    if shader.get('ramp'):
        out.append(shader['ramp'])
    vertex = shader.get('vertex')
    if vertex:
        out.append(vertex)
        if vertex.get('weight'):
            out.append(vertex['weight'])
    return out


def export_layers(root_go: int, read, *, mesh_of: Callable, texture_of: Callable, classify_texture: Callable,
                  external_of: Callable, shaders: dict, slots: list[str], shared: Callable | None = None, particles: bool) -> LayerExport:
    """The layers of the illustration prefab whose root GameObject is `root_go`.

    read(path_id) -> (type name, typetree) | None for any object in the bundle; mesh_of(path_id) ->
    {'vertices': [(x, y, z)], 'uv': [(u, v)], 'colors': [(r, g, b, a) in 0-1] | None, 'submeshes':
    [[index, ...]]}; texture_of(path_id) -> PIL image (RGBA as shipped); classify_texture(image) ->
    l2d.classify_alpha's result; external_of(ref) -> the CAB name (or 'unity default resources') a
    reference with m_FileID != 0 points into; shaders: shader_table() of the shared shader bundle;
    slots: the skeleton's slot names (the Spine runtime's reading), which separator names must match;
    shared(CAB) -> (read, texture_of[, external_of]) of the shared bundle with that CAB name, or None (a layer
    that needs it is left out under externalTexture; a particle material in it is left out). `particles`
    (required): also export the ParticleSystems (scripts/particles.py: LayerExport.particles, the
    {"particles"} draw runs and omitted.particleReasons); False writes layers.json as before them.
    """
    return _Exporter(root_go, read, mesh_of=mesh_of, texture_of=texture_of, classify_texture=classify_texture,
                     external_of=external_of, shaders=shaders, slots=slots, shared=shared, particles=particles).run()


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
        normals = [tuple(float(x) for x in (list(n) + [0.0, 0.0])[:3]) for n in handler.m_Normals] if handler.m_Normals else None
        return {'vertices': vertices, 'uv': [tuple(float(x) for x in uv[:2]) for uv in handler.m_UV0], 'colors': colours, 'submeshes': submeshes,
                'normals': normals}

    def texture_of(path_id: int):
        return objects[path_id].read().image.convert('RGBA')

    return mesh_of, texture_of, external_of


class SharedTextures:
    """The client's shared FX bundles (refs/fx/texture/..., and refs/fx/material.ab and sharedbattle.ab for
    particle materials), which effect materials take their noise, dissolve, ramp and some main textures
    from: `table` maps a CAB name to its bundle name (shared-bundles.json, written by
    scripts/shared_bundles.py); `fetch(bundle name)` returns the unpacked, md5-checked bundle or None. Each
    is fetched and decoded once, on first use. Callable as the exporter's `shared`: CAB -> (read,
    texture_of, external_of) of that bundle, external_of naming the CABs its own references point into."""

    def __init__(self, table: dict, fetch: Callable, unitypy):
        self.table = table
        self.fetch = fetch
        self.unitypy = unitypy
        self.loaded = {}  # CAB -> (read, texture_of) or None
        self.used = set()  # bundle names actually read

    def __call__(self, cab: str):
        if cab in self.loaded:
            return self.loaded[cab]
        bundle = self.table.get(cab)
        source = None
        if bundle:
            data = self.fetch(bundle)
            if data is not None:
                env = self.unitypy.load(data)
                if cab_name(env) != cab:
                    raise LayerError(f'{bundle} is {cab_name(env)}, not {cab} (shared-bundles.json is out of date)')
                objects = {o.path_id: o for o in env.objects}
                trees = {}

                def read(path_id, objects=objects, trees=trees):
                    if path_id not in trees:
                        obj = objects.get(path_id)
                        trees[path_id] = (obj.type.name, obj.read_typetree()) if obj is not None else None
                    return trees[path_id]

                def texture_of(path_id, objects=objects):
                    return objects[path_id].read().image.convert('RGBA')

                source = (read, texture_of, bundle_readers(env, objects)[2])
                self.used.add(bundle)
        self.loaded[cab] = source
        return source


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
