"""Tests for scripts/effects.py: each effect shader the site reproduces, read from synthetic materials.

Run: python -m unittest discover -s test -p 'test_*.py'
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import effects  # noqa: E402
import layers  # noqa: E402

TEX = {'id': 5, 'w': 256, 'h': 256}
NOISE = {'external': 'CAB-c2c688f710a565b7cce0cc93f7bd219f', 'id': 9}


def unbound_defaults(shader: str) -> dict:
    """The textures a shader samples where nothing is bound, as the client's shader bundle declares them: every
    dissolve map white, except Dissolve(CustomData)'s, black."""
    return {'_MainTex': 'white', '_DissolveTex': 'black' if shader.endswith('Dissolve(CustomData)') else 'white',
            '_DissolveTex_01': 'white', '_DissolveTex_02': 'white'}


def material(shader, *, floats=None, colors=None, textures=None, keywords='', defaults=None):
    """A material on `shader` (a name), textures {name: (ref, scale, offset)}."""
    envs = {'_MainTex': {'tex': TEX, 'scale': [1, 1], 'offset': [0, 0]}}
    for name, (tex, scale, offset) in (textures or {}).items():
        envs[name] = {'tex': tex, 'scale': list(scale), 'offset': list(offset)}
    return layers.Material(name='m', shader=layers.Shader(shader, 5.0, 10.0, 0.0, 3000, defaults or {}, unbound_defaults(shader)), shader_ref='x',
                           keywords=keywords, floats=floats or {}, colors=colors or {}, textures=envs, queue=3000)


def tex(ref=NOISE, scale=(1, 1), offset=(0, 0)):
    return (ref, scale, offset)


def layer_describe(m, animated=frozenset()):
    """effects.describe as a mesh layer calls it: no particle data (custom None)."""
    return effects.describe(m, animated, custom=None)


class Rounding(unittest.TestCase):
    def test_round_even_and_the_dissolve_k(self):
        self.assertEqual([effects.round_even(x) for x in (0.5, 1.5, 2.5, -0.5, 0.4, 0.6)], [0, 2, 2, 0, 0, 1])
        # k = 1 - roundEven(amount + 0.5): 1 at amount 0, 0 inside (0, 1), -1 at 1.
        self.assertEqual([effects.dissolve_k(a) for a in (0.0, 0.1, 0.5, 0.99, 1.0)], [1, 0, 0, 0, -1])
        self.assertEqual(effects.dissolve_constant(0.0, 0.1, 1.0), 1.0)
        self.assertAlmostEqual(effects.dissolve_constant(0.95, 0.1, 1.0), 0.5)
        self.assertEqual(effects.dissolve_constant(1.0, 0.1, 1.0), 0.0)
        # Over black (Dissolve(CustomData)'s default) anything above amount 0 is gone.
        self.assertEqual(effects.dissolve_constant(0.0, 0.1, 0.0), 1.0)
        self.assertEqual(effects.dissolve_constant(0.25, 0.1, 0.0), 0.0)


class Families(unittest.TestCase):
    def test_disturb_flows_after_the_main_tiling_with_weight_and_dissolve(self):
        m = material(effects.L2D + 'Disturb/Disturb(CustomData)', floats={'_IntensityU': 0.2, '_DisturbScale': 0.5, '_DisturbVSpeed': -0.3, '_Amount': 0.4,
                                                                           '_DisturbInfluenceDissolveUV': 1.0, '_Opacity': 0.8, '_MainUSpeed': 0.1},
                     textures={'_DisturbTex': tex(scale=(2, 2)), '_WeightTex': tex({'id': 6, 'w': 64, 'h': 64}), '_DissolveTex': tex(offset=(0.5, 0))})
        e = layer_describe(m)
        self.assertEqual((e['family'], e['opacity'], e['main']['speed']), ('particle', 0.8, [0.1, 0.0]))
        d = e['distort']
        self.assertEqual((d['space'], d['main'], d['dissolve'], d['maps'][0]['st'], d['maps'][0]['speed'], d['maps'][0]['intensity']),
                         ('main', 0.5, 1.0, [2, 2, 0, 0], [0.0, -0.3], [0.2, 0.0]))
        self.assertEqual(d['weight']['tex']['id'], 6)
        self.assertEqual([(x['amount'], x['border'], x['st']) for x in e['dissolve']], [(0.4, 0.1, [1, 1, 0.5, 0])])
        # No noise bound (the default is black): no distortion; the weight is never sampled then.
        self.assertIsNone(layer_describe(material(effects.L2D + 'Disturb/Disturb(CustomData)', floats={'_IntensityU': 0.2}))['distort'])

    def test_ram_multiplies_its_ramp_and_vertex_disturb_moves_vertices(self):
        ram = layer_describe(material(effects.L2D + 'Ram/Disturb(CustomData)', textures={'_RamTex': tex(scale=(1, 0.5))}))
        self.assertEqual(ram['ramp']['st'], [1, 0.5, 0, 0])
        self.assertIsNone(ram['vertex'])
        v = layer_describe(material(effects.L2D + 'Ram/VertexDisturb(CustomData)', colors={'_VertexDisturbIntensity': [0.1, 0.2, 0.0, 0.0]},
                                      floats={'_VertexDisturbUSpeed': 0.5, '_IntensityV': 0.1, '_DisturbInfluenceMainUV': 0.0},
                                      textures={'_VertexDisturbTex': tex(), '_DisturbTex': tex()}))
        self.assertEqual((v['vertex']['intensity'], v['vertex']['speed'], v['vertex']['weight']), ([0.1, 0.2, 0.0], [0.5, 0.0], None))
        self.assertEqual(v['distort']['main'], 1.0, 'VertexDisturb moves the main UV whatever _DisturbInfluenceMainUV says')

    def test_anchor_distorts_the_mesh_uv_around_its_anchors(self):
        m = material(effects.L2D + 'Disturb/Disturb Anchor (Add)', keywords='_WEIGHT_ON',
                     floats={'_IntensityU': 0.1, '_AnchorU': 0.4, '_ToggleUseDisturb2': 1.0, '_IntensityV_02': 0.3, '_ToggleUseDissolve': 1.0, '_Amount': 0.2},
                     colors={'_MainColor': [1, 1, 1, 0.5], '_UVTween': [0.0, 0.1, 0.2, 0.0]},
                     textures={'_DisturTex': tex(), '_WeightTex': tex({'id': 7, 'w': 8, 'h': 8}), '_DissolveTex': tex()},
                     defaults={'_MainColorACtrl': 1.0, '_AnchorV': 0.5, '_AnchorU_02': 0.5, '_AnchorV_02': 0.5, '_BorderWidth': 0.1})
        e = layer_describe(m)
        d = e['distort']
        # The second noise is unbound but weighted: it reads 0, a constant the weight scales per texel.
        self.assertEqual([(x['name'], x['tex'] is not None, x['anchor'], x['intensity'], x['speed']) for x in d['maps']],
                         [('_DisturTex', True, [0.4, 0.5], [0.1, 0.0], [0.2, 0.0]), ('_DisturTex_02', False, [0.5, 0.5], [0.0, 0.3], [0.0, 0.0])])
        self.assertEqual((d['space'], d['dissolve'], e['main']['speed'], e['rgb_scale']), ('raw', 1.0, [0.0, 0.1], 0.5))
        # Unweighted, an unbound noise is a constant offset folded into `constant`.
        e2 = layer_describe(material(effects.L2D + 'Disturb/Disturb Anchor (AlphaBlend)', floats={'_IntensityU': 0.1, '_AnchorU': 0.4},
                                       defaults={'_AnchorV': 0.5}))
        self.assertEqual((e2['distort']['maps'], e2['distort']['constant']), ([], [-0.04000000000000001, -0.0]))

    def test_dissolve_shaders(self):
        tween = layer_describe(material(effects.L2D + 'Dissolve/Dissolve Add UVTween', floats={'_Amount': 0.5},
                                          colors={'_UVTween': [0.1, 0, 0, 0.2]}, textures={'_DissolveTex': tex()}))
        self.assertEqual((tween['color_property'], tween['main'], tween['dissolve'][0]['speed'], tween['dissolve'][0]['fract']),
                         ('_TintColor', {'speed': [0.1, 0], 'fract': True, 'speed_names': [('_UVTween', 0, 1.0), ('_UVTween', 1, 1.0)]}, [0.0, 0.2], True))
        # Its speeds come from _UVTween: xy the main texture's, zw the dissolve's (an Animator that drives them
        # moves an integrated offset, parameters()).
        self.assertEqual(tween['dissolve'][0]['speed_names'], [('_UVTween', 2, 1.0), ('_UVTween', 3, 1.0)])
        double = layer_describe(material(effects.L2D + 'Dissolve/Dissolve AB Double edge', floats={'_Amount_01': 0.3, '_Amount_02': 0.6, '_pow': 2.0},
                                           colors={'_Edgecolor': [1, 0.5, 0, 0.2]}, textures={'_DissolveTex_01': tex(), '_DissolveTex_02': tex()},
                                           defaults={'_BorderWidth_01': 0.1, '_BorderWidth_02': 0.2}))
        self.assertEqual([(d['name'], d['amount'], d['border']) for d in double['dissolve']], [('_DissolveTex_01', 0.3, 0.1), ('_DissolveTex_02', 0.6, 0.2)])
        self.assertEqual(double['edge'], {'color': [1, 0.5, 0, 0.2], 'pow': 2.0, 'epsilon': True})
        # Without its texture a dissolve is a constant factor; amount 1 and more hides the layer.
        half = layer_describe(material(effects.L2D + 'Dissolve/Dissolve AB', floats={'_Amount': 0.95}, defaults={'_BorderWidth': 0.1}))
        self.assertEqual(half['dissolve'], [])
        self.assertAlmostEqual(half['alpha_scale'], 0.5)
        gone = layer_describe(material(effects.L2D + 'Dissolve/Dissolve AB', floats={'_Amount': 1.2}, textures={'_DissolveTex': tex()}))
        self.assertEqual((gone['dissolve'], gone['alpha_scale']), ([], 0.0))
        cd = layer_describe(material(effects.L2D + 'Dissolve/Dissolve(CustomData)', floats={'_UseDissolveTex': 1.0, '_DissolveIntensity': 0.25, '_Opacity': 0.5},
                                       textures={'_DissolveTex': tex()}))
        self.assertEqual((cd['color_property'], cd['opacity'], cd['dissolve'][0]['amount']), ('_MainColor', 0.5, 0.25))
        self.assertEqual(layer_describe(material(effects.L2D + 'Dissolve/Dissolve(CustomData)', textures={'_DissolveTex': tex()}))['dissolve'], [])
        with self.assertRaisesRegex(effects.Unsupported, 'dissolve blended'):
            layer_describe(material(effects.L2D + 'Dissolve/Dissolve(CustomData)', floats={'_UseDissolveTex': 0.5}))
        # Unbound, Dissolve(CustomData) reads its default black texture: amount 0.25 hides the layer.
        black = layer_describe(material(effects.L2D + 'Dissolve/Dissolve(CustomData)', floats={'_UseDissolveTex': 1.0, '_DissolveIntensity': 0.25}))
        self.assertEqual((black['dissolve'], black['alpha_scale']), ([], 0.0))

    def test_custom_data_on_an_unbound_dissolve_samples_the_shaders_default(self):
        # A particle's custom data moves the amount per particle, so the dissolve stays a map: one texel of the
        # default the shader declares (layers.py writes it as a 1x1 texture).
        for shader, amount, value in (('Disturb/Disturb(CustomData)', '_Amount', 1.0), ('Dissolve/Dissolve(CustomData)', '_DissolveIntensity', 0.0)):
            m = material(effects.L2D + shader, floats={'_UseDissolveTex': 1.0, amount: 0.3}, keywords='_HGCUSTOMVERTEXSTREAM_ON')
            d = effects.describe(m, frozenset(), custom=frozenset({amount}))['dissolve'][0]
            self.assertEqual((d['tex'], d['amount'], d['amount_name']), ({'solid': value}, 0.3, amount))
        unknown = material(effects.L2D + 'Disturb/Disturb(CustomData)', floats={'_Amount': 0.3}, keywords='_HGCUSTOMVERTEXSTREAM_ON')
        unknown.shader.textures['_DissolveTex'] = 'bump'
        with self.assertRaisesRegex(effects.Unsupported, 'default .bump. unknown'):
            effects.describe(unknown, frozenset(), custom=frozenset({'_Amount'}))

    def test_disturb2_is_the_noise_family_without_the_x2(self):
        n = layer_describe(material(effects.L2D + 'Disturb/Disturb2 (Add)', colors={'_Noise1Param': [2, 1, 0.5, 0.5]}, textures={'_DisturTex': tex()}))
        self.assertEqual((n['family'], n['mode'], n['rgb_scale'], n['noise1'], n['glow']), ('noise', 'add', 0.5, [2, 1, 0.5, 0.5], None))
        glow = layer_describe(material(effects.L2D + 'Disturb/Disturb2 (AlphaBlend)', keywords='_DISTURBMODE_GLOW', colors={'_GlowColor': [1, 0, 0, 1]}))
        self.assertEqual((glow['mode'], glow['glow'], glow['noise']), ('glow', [1, 0, 0, 1], None))

    def test_speeds_an_animator_drives_become_offsets_and_absent_stages_ignore_theirs(self):
        disturb = layer_describe(material(effects.L2D + 'Disturb/Disturb(CustomData)', floats={'_IntensityU': 0.2, '_Amount': 0.4},
                                            textures={'_DisturbTex': tex(), '_DissolveTex': tex()}))
        params = effects.parameters(disturb)
        self.assertEqual(params['main.offset'], ('offset', [('_MainUSpeed', None, 1.0), ('_MainVSpeed', None, 1.0)]))
        self.assertEqual(params['distort.maps.0.offset'][1][0][0], '_DisturbUSpeed')
        self.assertEqual(params['dissolve.0.offset'][1][1][0], '_DissolveVSpeed')
        self.assertEqual(params['distort.main'], ('float', ['_DisturbScale']))
        self.assertEqual(effects.width('distort.maps.0.offset'), 4, '[u, v, speed u, speed v]')
        self.assertFalse(effects.inert(disturb, '_DisturbUSpeed'))
        # No noise bound: no distortion, so its speed and influence change nothing.
        plain = layer_describe(material(effects.L2D + 'Disturb/Disturb(CustomData)', floats={'_IntensityU': 0.2}))
        self.assertTrue(effects.inert(plain, '_DisturbUSpeed'))
        self.assertTrue(effects.inert(plain, '_DisturbScale'))
        self.assertFalse(effects.inert(plain, '_MainUSpeed'))
        # Disturb2 scrolls each channel with _Time.x (t / 20) and never reads its noise's tiling.
        noise = layer_describe(material(effects.L2D + 'Disturb/Disturb2 (AlphaBlend)', textures={'_DisturTex': tex()}))
        self.assertEqual(effects.parameters(noise)['noise.offset'], ('offset', [('_Noise1Param', 1, 0.05), ('_Noise2Param', 1, 0.05)]))
        self.assertTrue(effects.inert(noise, '_DisturTex_ST'))
        anchor = layer_describe(material(effects.L2D + 'Disturb/Disturb Anchor (AlphaBlend)', floats={'_IntensityU': 0.1}, textures={'_DisturTex': tex()}))
        self.assertEqual(effects.parameters(anchor)['distort.maps.0.offset'][1], [('_UVTween', 2, 1.0), ('_UVTween', 3, 1.0)])

    def test_what_stays_out(self):
        for keywords, reason in (('_HG_UV_ROTATION', 'UV rotation'), ('_HGCUSTOMVERTEXSTREAM_ON', 'custom vertex stream'), ('HG_SPRITE_SHEET', 'sprite sheet')):
            with self.assertRaisesRegex(effects.Unsupported, reason):
                layer_describe(material(effects.L2D + 'Disturb/Disturb(CustomData)', keywords=keywords))
        with self.assertRaisesRegex(effects.Unsupported, 'shader Torappu/Particles-L2D/Disturb/Disturb GrabPass'):
            layer_describe(material(effects.L2D + 'Disturb/Disturb GrabPass'))
        with self.assertRaisesRegex(effects.Unsupported, 'border'):
            layer_describe(material(effects.L2D + 'Dissolve/Dissolve AB', floats={'_Amount': 0.5, '_BorderWidth': 0.0}, textures={'_DissolveTex': tex()}))
        textured = layer_describe(material(effects.L2D + 'Ram/Disturb(CustomData)', floats={'_IntensityU': 0.1, '_Amount': 0.3},
                                             textures={'_DisturbTex': tex(), '_DissolveTex': tex({'id': 3, 'w': 4, 'h': 4}), '_RamTex': tex()}))
        self.assertEqual(len(effects.textures_of(textured)), 3)


if __name__ == '__main__':
    unittest.main()
