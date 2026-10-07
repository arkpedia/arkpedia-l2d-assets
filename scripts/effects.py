"""The game's effect shaders the site reproduces: how a layer material that does more than texture x 2
x vertex colour x tint draws, as data (layers.json `effect` entries and a plain layer's `exact`).

Read line by line from the GLES3 GLSL of the Torappu shaders in the client's shared shader bundle
([uc]shaders.ab). Every shader below computes, in Unity's UV space (v up):

  colour = main(uv_main) x colour x vertex colour [x ramp(uv_ramp)]      (colour already holds the x2)
  alpha  = clamp(colour.a x dissolve_1 x dissolve_2)                     (an edge colour may mix in)

with the main UV moved by a flow distortion: a noise texture's red channel (minus an anchor), times
an intensity and a weight texture's red/green, added to the main UV after its tiling (Disturb,
Ram/Disturb, VertexDisturb: `space` 'main') or to the mesh UV before it (Disturb Anchor, the old
Disturb: 'raw'). A dissolve is clamp((texture.r - amount + border x k) / border), k = 1 -
roundEven(amount + 0.5). VertexDisturb also moves each vertex by a noise texture's rgb - 0.5 times
its weight and intensity, in the mesh's own units.

`describe(material)` returns that description with the material's texture references (resolved to
layers.json indices by layers.py) or raises Unsupported with the reason the layer stays out:
UV rotation and custom vertex streams (set by scripts and particle streams a mesh does not have),
grab passes, 3D lighting, shaders not listed here. Disturb2 (two-channel noise, different colour
maths) is the 'noise' family.

Time: every `speed` is the shader's own scroll, fract(_Time.y x speed) as the GLSL takes it; every
`scroll` is a UV scroll script's (offset += speed x dt, not wrapped). Both are Unity UV units per
second; the site adds them in Unity space and flips v only when it samples.
"""
from __future__ import annotations

PARTICLE = 'particle'
NOISE = 'noise'

L2D = 'Torappu/Particles-L2D/'
DISTURB = {L2D + 'Disturb/Disturb(CustomData)', 'Torappu/Particles/Disturb/Disturb(CustomData)'}
RAM = {L2D + 'Ram/Disturb(CustomData)', 'Torappu/Particles/Ram/Disturb(CustomData)'}
VERTEX = {L2D + 'Ram/VertexDisturb(CustomData)'}
ANCHOR = {L2D + 'Disturb/Disturb Anchor (AlphaBlend)', L2D + 'Disturb/Disturb Anchor (Add)'}
OLD_DISTURB = {L2D + 'Disturb/Disturb (AlphaBlend)', L2D + 'Disturb/Disturb (Add)'}
DISSOLVE = {L2D + 'Dissolve/Dissolve Add', L2D + 'Dissolve/Dissolve AB'}
DISSOLVE_TWEEN = {L2D + 'Dissolve/Dissolve Add UVTween', L2D + 'Dissolve/Dissolve AB UVTween'}
DISSOLVE_DOUBLE = {L2D + 'Dissolve/Dissolve Add Double', L2D + 'Dissolve/Dissolve AB Double'}
# The AB edge shader adds 0.001 to both sides of the edge ratio and keeps the edge at least 0.001.
DISSOLVE_EDGE = {L2D + 'Dissolve/Dissolve Add Double edge': False, L2D + 'Dissolve/Dissolve AB Double edge': True,
                 'Hidden/Particles-L2D/Dissolve/Dissolve AB Double edge': True}
DISSOLVE_CD = {L2D + 'Dissolve/Dissolve(CustomData)'}
DISTURB2 = {L2D + 'Disturb/Disturb2 (AlphaBlend)': 'default', L2D + 'Disturb/Disturb2 (Add)': 'add'}
SHADERS = DISTURB | RAM | VERTEX | ANCHOR | OLD_DISTURB | DISSOLVE | DISSOLVE_TWEEN | DISSOLVE_DOUBLE | set(DISSOLVE_EDGE) | DISSOLVE_CD | set(DISTURB2)


class Unsupported(Exception):
    """The material's shader does something the site does not reproduce; the message is why."""


def round_even(x: float) -> float:
    return float(round(x))  # Python rounds half to even, as GLSL's roundEven


def dissolve_k(amount: float) -> float:
    return 1.0 - round_even(amount + 0.5)


def dissolve_constant(amount: float, border: float, value: float = 1.0) -> float:
    """What a dissolve multiplies alpha by when its texture reads `value` everywhere (unbound: white)."""
    border = border or 1e-6
    return max(0.0, min(1.0, (value - amount + border * dissolve_k(amount)) / border))


def _st(m, name: str) -> list:
    env = m.textures.get(name) or {}
    return [*(env.get('scale') or [1.0, 1.0]), *(env.get('offset') or [0.0, 0.0])]


def _map(m, name: str, speed=(0.0, 0.0), **extra) -> dict:
    return {'name': name, 'tex': m.texture(name), 'st': _st(m, name), 'speed': [float(speed[0]), float(speed[1])], **extra}


def _colour(m, name: str, default: float = 0.5) -> list:
    return list(m.colors.get(name) or [m.shader.defaults.get(name, default) if m.shader else default] * 4)


def _dissolve(m, texture: str, amount: str, border: str, *, speed=(0.0, 0.0), fract=False, amount_default=0.5, border_default=0.1,
              animated=frozenset()):
    """(map or None, constant alpha factor): a dissolve with a texture is a map; without one (white)
    or with nothing to dissolve it is a constant. One whose amount or border an Animator drives is
    always a map (its frames carry the values)."""
    a = m.float(amount, amount_default)
    b = m.float(border, border_default)
    moving = bool({amount, border, f'{texture}_ST'} & animated)
    if moving and not m.texture(texture) and {amount, border} & animated:
        raise Unsupported(f'animated dissolve without its texture ({amount})')
    if not moving:
        if a <= 0:
            return None, 1.0
        if not m.texture(texture):
            return None, dissolve_constant(a, b)
    if b <= 0:
        raise Unsupported(f'dissolve border {b:g}')
    if a >= 1 and not moving:
        return None, 0.0  # fully dissolved (k = -1 from amount 1 on): nothing shows
    if not m.texture(texture):
        return None, dissolve_constant(a, b)  # only its tiling moves: still a constant
    return _map(m, texture, speed, fract=fract, amount=a, border=b, amount_name=amount, border_name=border), 1.0


def _keywords(m) -> set:
    return set((m.keywords or '').split())


def describe(m, animated=frozenset()) -> dict:
    """The effect a material draws with: {'family', 'color_property', 'rgb_scale', 'alpha_scale',
    'opacity', 'main': {'speed', 'fract'}, 'distort', 'dissolve', 'edge', 'ramp', 'vertex'} (family
    'particle'), or Disturb2's {'family': 'noise', ...}. Texture members are the material's own
    references ({'id', 'w', 'h'} or {'external': CAB, 'id'}), resolved by layers.py. Raises
    Unsupported for anything this does not reproduce. `animated`: the material properties an Animator
    drives on this renderer; a stage they drive exists even where the material's own values switch it
    off (parameters() names where their values go)."""
    if m.shader is None:
        raise Unsupported(f'shader not found ({m.shader_ref})')
    name = m.shader.name
    keywords = _keywords(m)
    if '_HG_UV_ROTATION' in keywords:
        raise Unsupported('UV rotation (_HG_UV_ROTATION)')
    if '_HGCUSTOMVERTEXSTREAM_ON' in keywords:
        raise Unsupported('custom vertex stream (_HGCUSTOMVERTEXSTREAM_ON)')
    if 'HG_SPRITE_SHEET' in keywords:
        raise Unsupported('sprite sheet (HG_SPRITE_SHEET)')
    if name not in SHADERS:
        raise Unsupported(f'shader {name}')
    if name in DISTURB2:
        return _noise(m, DISTURB2[name], keywords)
    out = {'family': PARTICLE, 'color_property': '_MainColor', 'rgb_scale': 1.0, 'alpha_scale': 1.0, 'opacity': 1.0,
           'main': {'speed': [0.0, 0.0], 'fract': False}, 'distort': None, 'dissolve': [], 'edge': None, 'ramp': None, 'vertex': None}

    if name in DISTURB or name in RAM or name in VERTEX:
        out['opacity'] = m.float('_Opacity', 1.0)
        out['main']['speed'] = [m.float('_MainUSpeed'), m.float('_MainVSpeed')]
        dissolve, constant = _dissolve(m, '_DissolveTex', '_Amount', '_BorderWidth', speed=(m.float('_DissolveUSpeed'), m.float('_DissolveVSpeed')),
                                       animated=animated)
        out['alpha_scale'] *= constant
        if dissolve:
            out['dissolve'].append(dissolve)
        iu, iv = m.float('_IntensityU'), m.float('_IntensityV')
        influence = 1.0 if name in VERTEX else m.float('_DisturbScale', 1.0) if name in DISTURB else m.float('_DisturbInfluenceMainUV', 1.0)
        dissolve_influence = m.float('_DisturbInfluenceDissolveUV')
        moving = bool({'_IntensityU', '_IntensityV', '_DisturbTex_ST'} & animated)
        if m.texture('_DisturbTex') and (iu or iv or moving) and (influence or (dissolve and dissolve_influence)):
            weight = _map(m, '_WeightTex') if name in DISTURB and m.texture('_WeightTex') else None
            out['distort'] = {'space': 'main', 'main': influence, 'dissolve': dissolve_influence if dissolve else 0.0,
                              'maps': [_map(m, '_DisturbTex', (m.float('_DisturbUSpeed'), m.float('_DisturbVSpeed')), anchor=[0.0, 0.0], intensity=[iu, iv],
                                            intensity_names=['_IntensityU', '_IntensityV'])],
                              'weight': weight}
        if name in RAM or name in VERTEX:
            if m.texture('_RamTex'):
                out['ramp'] = _map(m, '_RamTex')
        if name in VERTEX:
            intensity = list((m.colors.get('_VertexDisturbIntensity') or [0.0, 0.0, 0.0, 0.0])[:3])
            if m.texture('_VertexDisturbTex') and (any(intensity) or {'_VertexDisturbIntensity', '_VertexDisturbTex_ST'} & animated):
                out['vertex'] = _map(m, '_VertexDisturbTex', (m.float('_VertexDisturbUSpeed'), m.float('_VertexDisturbVSpeed')), intensity=intensity,
                                     weight=_map(m, '_VertexDisturbWeightTex') if m.texture('_VertexDisturbWeightTex') else None)
        return out

    if name in ANCHOR:
        main_color = _colour(m, '_MainColor')
        gain = m.float('_MainColorACtrl', 1.0) * (main_color[3] - 1) + 1
        out['rgb_scale'] = out['alpha_scale'] = gain
        tween = m.colors.get('_UVTween') or [0.0, 0.0, 0.0, 0.0]
        out['main']['speed'] = [tween[0], tween[1]]
        weighted = '_WEIGHT_ON' in keywords and m.texture('_WeightTex')
        maps, constant = [], [0.0, 0.0]
        sources = [('_DisturTex', '_IntensityU', '_IntensityV', '_AnchorU', '_AnchorV', None, (tween[2], tween[3])),
                   ('_DisturTex_02', '_IntensityU_02', '_IntensityV_02', '_AnchorU_02', '_AnchorV_02', '_ToggleUseDisturb2', (0.0, 0.0))]
        for texture, iu_, iv_, au_, av_, toggle, speed in sources:
            if toggle and not m.float(toggle) > 0:
                continue
            iu, iv, au, av = m.float(iu_), m.float(iv_), m.float(au_, 0.5), m.float(av_, 0.5)
            moving = bool({iu_, iv_, au_, av_, f'{texture}_ST'} & animated)
            if not (iu or iv or moving):
                continue
            if m.texture(texture):
                maps.append(_map(m, texture, speed, anchor=[au, av], intensity=[iu, iv], intensity_names=[iu_, iv_]))
            elif weighted or moving:
                # An unbound noise reads 0: a constant offset, scaled by the weight texture per texel.
                maps.append({'name': texture, 'tex': None, 'st': [1.0, 1.0, 0.0, 0.0], 'speed': [0.0, 0.0], 'anchor': [au, av], 'intensity': [iu, iv],
                             'intensity_names': [iu_, iv_]})
            else:
                constant[0] -= au * iu
                constant[1] -= av * iv
        if m.float('_ToggleUseDissolve'):
            dissolve, factor = _dissolve(m, '_DissolveTex', '_Amount', '_BorderWidth', animated=animated)
            out['alpha_scale'] *= factor
            if dissolve:
                out['dissolve'].append(dissolve)
        if maps or any(constant):
            out['distort'] = {'space': 'raw', 'main': 1.0, 'dissolve': 1.0 if out['dissolve'] else 0.0, 'maps': maps,
                              'weight': _map(m, '_WeightTex') if weighted else None, 'constant': constant}
        return out

    if name in OLD_DISTURB:
        main_color = _colour(m, '_MainColor')
        # texture x colour x vertex colour x colour alpha x 2.
        out['rgb_scale'] = out['alpha_scale'] = main_color[3]
        iu, iv = m.float('_IntensityU'), m.float('_IntensityV')
        if m.texture('_DisturTex') and (iu or iv or {'_IntensityU', '_IntensityV', '_DisturTex_ST'} & animated):
            out['distort'] = {'space': 'raw', 'main': 1.0, 'dissolve': 0.0, 'weight': None, 'constant': [0.0, 0.0],
                              'maps': [_map(m, '_DisturTex', anchor=[0.0, 0.0], intensity=[iu, iv], intensity_names=['_IntensityU', '_IntensityV'])]}
        return out

    out['color_property'] = '_TintColor'
    if name in DISSOLVE or name in DISSOLVE_TWEEN:
        tween = m.colors.get('_UVTween') or [0.0, 0.0, 0.0, 0.0] if name in DISSOLVE_TWEEN else [0.0, 0.0, 0.0, 0.0]
        fract = name in DISSOLVE_TWEEN
        out['main'] = {'speed': [tween[0], tween[1]], 'fract': fract}
        dissolve, factor = _dissolve(m, '_DissolveTex', '_Amount', '_BorderWidth', speed=(tween[2], tween[3]), fract=fract, animated=animated)
        out['alpha_scale'] *= factor
        if dissolve:
            out['dissolve'].append(dissolve)
        return out
    if name in DISSOLVE_DOUBLE or name in DISSOLVE_EDGE:
        for suffix in ('_01', '_02'):
            dissolve, factor = _dissolve(m, '_DissolveTex' + suffix, '_Amount' + suffix, '_BorderWidth' + suffix, animated=animated)
            out['alpha_scale'] *= factor
            if dissolve:
                out['dissolve'].append(dissolve)
        if name in DISSOLVE_EDGE:
            edge = _colour(m, '_Edgecolor', 1.0)
            if not DISSOLVE_EDGE[name] and edge[3] <= 0:
                raise Unsupported('dissolve edge with alpha 0')
            out['edge'] = {'color': edge, 'pow': m.float('_pow', 1.0), 'epsilon': DISSOLVE_EDGE[name]}
        return out
    if name in DISSOLVE_CD:
        out['color_property'] = '_MainColor'
        out['opacity'] = m.float('_Opacity', 1.0)
        out['main']['speed'] = [m.float('_MainUSpeed'), m.float('_MainVSpeed')]
        use = m.float('_UseDissolveTex')
        if use not in (0.0, 1.0):
            raise Unsupported(f'dissolve blended {use:g}')
        if use:
            dissolve, factor = _dissolve(m, '_DissolveTex', '_DissolveIntensity', '_BorderWidth', speed=(m.float('_DissolveUSpeed'), m.float('_DissolveVSpeed')),
                                         animated=animated)
            out['alpha_scale'] *= factor
            if dissolve:
                out['dissolve'].append(dissolve)
        return out
    raise Unsupported(f'shader {name}')


def _noise(m, mode: str, keywords: set) -> dict:
    """Disturb2: two noise channels (red at noise 1's scale and speed, green at noise 2's) move the main
    UV by 0.1 x their intensities; colour is the texture x _MainColor (no x2) with vertex alpha, or in
    glow mode texture.r x _MainColor + texture.g x _GlowColor. Its noise scrolls with _Time.x
    (seconds / 20)."""
    glow = '_DISTURBMODE_GLOW' in keywords
    if glow:
        mode = 'glow'  # the same in both passes
    n1 = list(m.colors.get('_Noise1Param') or [1.0, 1.0, 1.0, 1.0])
    n2 = list(m.colors.get('_Noise2Param') or [1.0, 1.0, 1.0, 1.0])
    # layers.json colours are the material's x 2 (scripts/layers.py doubles them); Disturb2 has no x2.
    return {'family': NOISE, 'mode': mode, 'color_property': '_MainColor', 'rgb_scale': 0.5, 'alpha_scale': 0.5,
            'noise': _map(m, '_DisturTex') if m.texture('_DisturTex') else None,
            'noise1': n1, 'noise2': n2, 'glow': _colour(m, '_GlowColor') if glow else None}


def parameters(effect: dict) -> dict:
    """Where an animated material property's values go: {layers.json path: (kind, [property names])}.
    kind 'float' (one value per property), 'vector' (a property's x, y, z, w; width given by the path)
    or 'color'. Paths: dissolve.<i>.amount|border|st, distort.maps.<i>.st|intensity, distort.weight.st,
    ramp.st, vertex.st|intensity, vertex.weight.st, edge.color. Disturb2's are not animated."""
    out = {}
    if effect['family'] != PARTICLE:
        return out
    for i, d in enumerate(effect.get('dissolve') or []):
        out[f'dissolve.{i}.amount'] = ('float', [d['amount_name']])
        out[f'dissolve.{i}.border'] = ('float', [d['border_name']])
        out[f'dissolve.{i}.st'] = ('vector', [d['name'] + '_ST'])
    distort = effect.get('distort')
    if distort:
        for i, d in enumerate(distort['maps']):
            if d.get('tex'):
                out[f'distort.maps.{i}.st'] = ('vector', [d['name'] + '_ST'])
            out[f'distort.maps.{i}.intensity'] = ('float', list(d['intensity_names']))
        if distort.get('weight'):
            out['distort.weight.st'] = ('vector', [distort['weight']['name'] + '_ST'])
    if effect.get('ramp'):
        out['ramp.st'] = ('vector', [effect['ramp']['name'] + '_ST'])
    vertex = effect.get('vertex')
    if vertex:
        out['vertex.st'] = ('vector', [vertex['name'] + '_ST'])
        out['vertex.intensity'] = ('vector', ['_VertexDisturbIntensity'])
        if vertex.get('weight'):
            out['vertex.weight.st'] = ('vector', [vertex['weight']['name'] + '_ST'])
    if effect.get('edge'):
        out['edge.color'] = ('color', ['_Edgecolor'])
    return out


# How many numbers each animated path adds to a frame.
WIDTHS = {'amount': 1, 'border': 1, 'st': 4, 'intensity': 2, 'color': 4}


def width(path: str) -> int:
    return 3 if path == 'vertex.intensity' else WIDTHS[path.rsplit('.', 1)[-1]]


def textures_of(effect: dict) -> list[dict]:
    """Every texture-bearing member of a description (maps with a 'tex'), for resolving and checks."""
    out = []
    if effect['family'] == NOISE:
        return [effect['noise']] if effect.get('noise') else []
    distort = effect.get('distort')
    if distort:
        out += [m for m in distort['maps'] if m.get('tex')]
        if distort.get('weight'):
            out.append(distort['weight'])
    out += effect.get('dissolve') or []
    if effect.get('ramp'):
        out.append(effect['ramp'])
    vertex = effect.get('vertex')
    if vertex:
        out.append(vertex)
        if vertex.get('weight'):
            out.append(vertex['weight'])
    return out
