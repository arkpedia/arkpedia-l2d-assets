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


def material(shader, *, floats=None, colors=None, textures=None, keywords='', defaults=None):
    """A material on `shader` (a name), textures {name: (ref, scale, offset)}."""
    envs = {'_MainTex': {'tex': TEX, 'scale': [1, 1], 'offset': [0, 0]}}
    for name, (tex, scale, offset) in (textures or {}).items():
        envs[name] = {'tex': tex, 'scale': list(scale), 'offset': list(offset)}
    return layers.Material(name='m', shader=layers.Shader(shader, 5.0, 10.0, 0.0, 3000, defaults or {}), shader_ref='x', keywords=keywords,
                           floats=floats or {}, colors=colors or {}, textures=envs, queue=3000)


def tex(ref=NOISE, scale=(1, 1), offset=(0, 0)):
    return (ref, scale, offset)


class Rounding(unittest.TestCase):
    def test_round_even_and_the_dissolve_k(self):
        self.assertEqual([effects.round_even(x) for x in (0.5, 1.5, 2.5, -0.5, 0.4, 0.6)], [0, 2, 2, 0, 0, 1])
        # k = 1 - roundEven(amount + 0.5): 1 at amount 0, 0 inside (0, 1), -1 at 1.
        self.assertEqual([effects.dissolve_k(a) for a in (0.0, 0.1, 0.5, 0.99, 1.0)], [1, 0, 0, 0, -1])
        self.assertEqual(effects.dissolve_constant(0.0, 0.1), 1.0)
        self.assertAlmostEqual(effects.dissolve_constant(0.95, 0.1), 0.5)
        self.assertEqual(effects.dissolve_constant(1.0, 0.1), 0.0)


class Families(unittest.TestCase):
    def test_disturb_flows_after_the_main_tiling_with_weight_and_dissolve(self):
        m = material(effects.L2D + 'Disturb/Disturb(CustomData)', floats={'_IntensityU': 0.2, '_DisturbScale': 0.5, '_DisturbVSpeed': -0.3, '_Amount': 0.4,
                                                                           '_DisturbInfluenceDissolveUV': 1.0, '_Opacity': 0.8, '_MainUSpeed': 0.1},
                     textures={'_DisturbTex': tex(scale=(2, 2)), '_WeightTex': tex({'id': 6, 'w': 64, 'h': 64}), '_DissolveTex': tex(offset=(0.5, 0))})
        e = effects.describe(m)
        self.assertEqual((e['family'], e['opacity'], e['main']['speed']), ('particle', 0.8, [0.1, 0.0]))
        d = e['distort']
        self.assertEqual((d['space'], d['main'], d['dissolve'], d['maps'][0]['st'], d['maps'][0]['speed'], d['maps'][0]['intensity']),
                         ('main', 0.5, 1.0, [2, 2, 0, 0], [0.0, -0.3], [0.2, 0.0]))
        self.assertEqual(d['weight']['tex']['id'], 6)
        self.assertEqual([(x['amount'], x['border'], x['st']) for x in e['dissolve']], [(0.4, 0.1, [1, 1, 0.5, 0])])
        # No noise bound (the default is black): no distortion; the weight is never sampled then.
        self.assertIsNone(effects.describe(material(effects.L2D + 'Disturb/Disturb(CustomData)', floats={'_IntensityU': 0.2}))['distort'])

    def test_ram_multiplies_its_ramp_and_vertex_disturb_moves_vertices(self):
        ram = effects.describe(material(effects.L2D + 'Ram/Disturb(CustomData)', textures={'_RamTex': tex(scale=(1, 0.5))}))
        self.assertEqual(ram['ramp']['st'], [1, 0.5, 0, 0])
        self.assertIsNone(ram['vertex'])
        v = effects.describe(material(effects.L2D + 'Ram/VertexDisturb(CustomData)', colors={'_VertexDisturbIntensity': [0.1, 0.2, 0.0, 0.0]},
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
        e = effects.describe(m)
        d = e['distort']
        # The second noise is unbound but weighted: it reads 0, a constant the weight scales per texel.
        self.assertEqual([(x['name'], x['tex'] is not None, x['anchor'], x['intensity'], x['speed']) for x in d['maps']],
                         [('_DisturTex', True, [0.4, 0.5], [0.1, 0.0], [0.2, 0.0]), ('_DisturTex_02', False, [0.5, 0.5], [0.0, 0.3], [0.0, 0.0])])
        self.assertEqual((d['space'], d['dissolve'], e['main']['speed'], e['rgb_scale']), ('raw', 1.0, [0.0, 0.1], 0.5))
        # Unweighted, an unbound noise is a constant offset folded into `constant`.
        e2 = effects.describe(material(effects.L2D + 'Disturb/Disturb Anchor (AlphaBlend)', floats={'_IntensityU': 0.1, '_AnchorU': 0.4},
                                       defaults={'_AnchorV': 0.5}))
        self.assertEqual((e2['distort']['maps'], e2['distort']['constant']), ([], [-0.04000000000000001, -0.0]))

    def test_dissolve_shaders(self):
        tween = effects.describe(material(effects.L2D + 'Dissolve/Dissolve Add UVTween', floats={'_Amount': 0.5},
                                          colors={'_UVTween': [0.1, 0, 0, 0.2]}, textures={'_DissolveTex': tex()}))
        self.assertEqual((tween['color_property'], tween['main'], tween['dissolve'][0]['speed'], tween['dissolve'][0]['fract']),
                         ('_TintColor', {'speed': [0.1, 0], 'fract': True}, [0.0, 0.2], True))
        double = effects.describe(material(effects.L2D + 'Dissolve/Dissolve AB Double edge', floats={'_Amount_01': 0.3, '_Amount_02': 0.6, '_pow': 2.0},
                                           colors={'_Edgecolor': [1, 0.5, 0, 0.2]}, textures={'_DissolveTex_01': tex(), '_DissolveTex_02': tex()},
                                           defaults={'_BorderWidth_01': 0.1, '_BorderWidth_02': 0.2}))
        self.assertEqual([(d['name'], d['amount'], d['border']) for d in double['dissolve']], [('_DissolveTex_01', 0.3, 0.1), ('_DissolveTex_02', 0.6, 0.2)])
        self.assertEqual(double['edge'], {'color': [1, 0.5, 0, 0.2], 'pow': 2.0, 'epsilon': True})
        # Without its texture a dissolve is a constant factor; amount 1 and more hides the layer.
        half = effects.describe(material(effects.L2D + 'Dissolve/Dissolve AB', floats={'_Amount': 0.95}, defaults={'_BorderWidth': 0.1}))
        self.assertEqual(half['dissolve'], [])
        self.assertAlmostEqual(half['alpha_scale'], 0.5)
        gone = effects.describe(material(effects.L2D + 'Dissolve/Dissolve AB', floats={'_Amount': 1.2}, textures={'_DissolveTex': tex()}))
        self.assertEqual((gone['dissolve'], gone['alpha_scale']), ([], 0.0))
        cd = effects.describe(material(effects.L2D + 'Dissolve/Dissolve(CustomData)', floats={'_UseDissolveTex': 1.0, '_DissolveIntensity': 0.25, '_Opacity': 0.5},
                                       textures={'_DissolveTex': tex()}))
        self.assertEqual((cd['color_property'], cd['opacity'], cd['dissolve'][0]['amount']), ('_MainColor', 0.5, 0.25))
        self.assertEqual(effects.describe(material(effects.L2D + 'Dissolve/Dissolve(CustomData)', textures={'_DissolveTex': tex()}))['dissolve'], [])
        with self.assertRaisesRegex(effects.Unsupported, 'dissolve blended'):
            effects.describe(material(effects.L2D + 'Dissolve/Dissolve(CustomData)', floats={'_UseDissolveTex': 0.5}))

    def test_disturb2_is_the_noise_family_without_the_x2(self):
        n = effects.describe(material(effects.L2D + 'Disturb/Disturb2 (Add)', colors={'_Noise1Param': [2, 1, 0.5, 0.5]}, textures={'_DisturTex': tex()}))
        self.assertEqual((n['family'], n['mode'], n['rgb_scale'], n['noise1'], n['glow']), ('noise', 'add', 0.5, [2, 1, 0.5, 0.5], None))
        glow = effects.describe(material(effects.L2D + 'Disturb/Disturb2 (AlphaBlend)', keywords='_DISTURBMODE_GLOW', colors={'_GlowColor': [1, 0, 0, 1]}))
        self.assertEqual((glow['mode'], glow['glow'], glow['noise']), ('glow', [1, 0, 0, 1], None))

    def test_what_stays_out(self):
        for keywords, reason in (('_HG_UV_ROTATION', 'UV rotation'), ('_HGCUSTOMVERTEXSTREAM_ON', 'custom vertex stream'), ('HG_SPRITE_SHEET', 'sprite sheet')):
            with self.assertRaisesRegex(effects.Unsupported, reason):
                effects.describe(material(effects.L2D + 'Disturb/Disturb(CustomData)', keywords=keywords))
        with self.assertRaisesRegex(effects.Unsupported, 'shader Torappu/Particles-L2D/Disturb/Disturb GrabPass'):
            effects.describe(material(effects.L2D + 'Disturb/Disturb GrabPass'))
        with self.assertRaisesRegex(effects.Unsupported, 'border'):
            effects.describe(material(effects.L2D + 'Dissolve/Dissolve AB', floats={'_Amount': 0.5, '_BorderWidth': 0.0}, textures={'_DissolveTex': tex()}))
        textured = effects.describe(material(effects.L2D + 'Ram/Disturb(CustomData)', floats={'_IntensityU': 0.1, '_Amount': 0.3},
                                             textures={'_DisturbTex': tex(), '_DissolveTex': tex({'id': 3, 'w': 4, 'h': 4}), '_RamTex': tex()}))
        self.assertEqual(len(effects.textures_of(textured)), 3)


if __name__ == '__main__':
    unittest.main()
