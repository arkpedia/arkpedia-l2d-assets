"""Tests for scripts/layers.py on synthetic prefabs shaped like the game's illustration prefabs.

Run: python -m unittest discover -s test -p 'test_*.py'
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import entrance_camera as ec  # noqa: E402
import l2d  # noqa: E402
import layers  # noqa: E402
from test_camera import Scene, linear, ref  # noqa: E402

SHADERS_CAB = 'CAB-shaders'
OTHER_CAB = 'CAB-other'
EXTERNALS = {1: layers.BUILTIN_RESOURCES, 2: OTHER_CAB, 3: SHADERS_CAB}
ALPHA_BLEND, ADDITIVE, DISTURB, DISSOLVE_ADD, ERASE, ANCHOR = 1, 2, 3, 4, 5, 6
SHADERS = {
    (SHADERS_CAB, ALPHA_BLEND): layers.Shader('Torappu/Particles-L2D/AlphaBlend', 5.0, 10.0, 0.0, 3000, {'_TintColor': 0.5}),
    (SHADERS_CAB, ADDITIVE): layers.Shader('Torappu/Particles-L2D/Additive', 5.0, 1.0, 0.0, 3000, {'_TintColor': 0.5}),
    # Disturb(CustomData) takes its second blend factor and its cull from the material.
    (SHADERS_CAB, DISTURB): layers.Shader('Torappu/Particles-L2D/Disturb/Disturb(CustomData)', 5.0, '_DstBlend', '_CullMode', 3000,
                                          {'_MainColor': 0.5, '_Opacity': 1.0, '_DstBlend': 10.0, '_CullMode': 0.0, '_Amount': 0.0}),
    (SHADERS_CAB, DISSOLVE_ADD): layers.Shader('Torappu/Particles-L2D/Dissolve/Dissolve Add', 5.0, 1.0, 0.0, 3000, {'_TintColor': 0.5, '_Amount': 0.5}),
    (SHADERS_CAB, ERASE): layers.Shader('Torappu/Particles-L2D/Mask/Erase', 5.0, 10.0, 0.0, 3000, {'_Strength': 1.0}),
    (SHADERS_CAB, ANCHOR): layers.Shader('Torappu/Particles-L2D/Disturb/Disturb Anchor (AlphaBlend)', 5.0, 10.0, 0.0, 3000,
                                         {'_MainColor': 0.5, '_AnchorU': 0.5, '_AnchorV': 0.5, '_AnchorU_02': 0.5, '_AnchorV_02': 0.5}),
}


def external(ref_):
    return EXTERNALS.get(ref_.get('m_FileID'), f'file {ref_.get("m_FileID")}')


def colour(r, g, b, a):
    return {'r': r, 'g': g, 'b': b, 'a': a}


def material_tree(shader, texture=None, *, floats=(), colours=(), keywords='', st=((1, 1), (0, 0)), external_texture=False, extra_textures=()):
    envs = []
    if texture is not None:
        tex_ref = {'m_FileID': 2, 'm_PathID': 77} if external_texture else ref(texture)
        envs.append(['_MainTex', {'m_Texture': tex_ref, 'm_Scale': {'x': st[0][0], 'y': st[0][1]}, 'm_Offset': {'x': st[1][0], 'y': st[1][1]}}])
    for name, tex in extra_textures:
        envs.append([name, {'m_Texture': ref(tex), 'm_Scale': {'x': 1, 'y': 1}, 'm_Offset': {'x': 0, 'y': 0}}])
    return {'m_Name': 'mat', 'm_Shader': {'m_FileID': 3, 'm_PathID': shader}, 'm_ShaderKeywords': keywords, 'm_CustomRenderQueue': -1,
            'm_SavedProperties': {'m_TexEnvs': envs, 'm_Floats': [list(f) for f in floats], 'm_Colors': [[k, colour(*v)] for k, v in colours]}}


class Prefab(Scene):
    """An illustration prefab: a root that plays the skeleton (scale 0.01) through two parts renderers
    split at slot 'slotB' (and 'gone', which the skeleton lacks), with the illustration controller and
    layers built by the tests."""

    def __init__(self):
        super().__init__()
        self.texture = self.add('Texture2D', {'m_Name': 'sky', 'm_Width': 4, 'm_Height': 4, 'm_TextureSettings': {'m_WrapU': 1, 'm_WrapV': 0}})
        self.root_go, self.root = self.game_object('illust')
        self.data = self.add('MonoBehaviour', {'scale': 0.01})
        self.skeleton = self.add('MonoBehaviour', {'m_Enabled': 1, 'skeletonDataAsset': ref(self.data), 'separatorSlotNames': ['gone', 'slotB']})
        self.controller = {'m_Enabled': 1, '_particles': [], '_animators': [], '_holders': [{'_effectPath': 'x', 'action': 1}]}
        self.attach(self.root_go, self.skeleton)
        self.attach(self.root_go, self.add('MonoBehaviour', self.controller))
        parts = []
        for name, order in (('0', 0), ('1', 10)):
            go, _ = self.game_object(name, self.root)
            parts.append(self.renderer(go, [], order))
        self.attach(self.root_go, self.add('MonoBehaviour', {'m_Enabled': 1, 'partsRenderers': [ref(p) for p in parts]}))
        self.meshes = {}

    def renderer(self, go, materials, order=0, enabled=1, kind='MeshRenderer'):
        renderer = self.add(kind, {'m_GameObject': ref(go), 'm_Enabled': enabled, 'm_Materials': [ref(m) for m in materials],
                                   'm_SortingLayerID': 0, 'm_SortingOrder': order})
        self.attach(go, renderer)
        return renderer

    def quad(self, name, parent, material, order=0, *, position=(0, 0, 0), rotation=(0, 0, 0, 1), scale=(1, 1, 1), active=1, mesh=None):
        go, tr = self.game_object(name, parent, position, rotation, scale, active)
        mesh_ref = ref(mesh) if mesh else {'m_FileID': 1, 'm_PathID': layers.BUILTIN_QUAD}
        self.attach(go, self.add('MeshFilter', {'m_GameObject': ref(go), 'm_Mesh': mesh_ref}))
        self.renderer(go, [self.add('Material', material)], order)
        return go, tr

    def export(self):
        from PIL import Image

        def mesh_of(path_id):
            return self.meshes[path_id]

        def texture_of(path_id):
            image = Image.new('RGBA', (4, 4), (200, 100, 50, 0))
            image.putpixel((1, 1), (200, 100, 50, 255))
            return image
        return layers.export_layers(self.root_go, self.read, mesh_of=mesh_of, texture_of=texture_of, classify_texture=l2d.classify_alpha,
                                    external_of=external, shaders=SHADERS, slots=['slotA', 'slotB', 'slotC'])


def entries(result):
    return [e['part'] if 'part' in e else e['layer'] for e in result.document['draw']]


def layer_named(result, name):
    return next(e for e in entries(result) if isinstance(e, dict) and e['name'] == name)


class Shaders(unittest.TestCase):
    def test_blend_cull_queue_and_defaults_come_from_the_parsed_shader(self):
        tree = {'m_ParsedForm': {'m_Name': 'Torappu/Particles-L2D/Disturb/Disturb(CustomData)',
                                 'm_PropInfo': {'m_Props': [{'m_Name': '_MainColor', 'm_DefValue[0]': 0.5}, {'m_Name': '_DstBlend', 'm_DefValue[0]': 10.0}]},
                                 'm_SubShaders': [{'m_Tags': {'tags': [['QUEUE', 'Transparent+1']]}, 'm_Passes': [{'m_State': {
                                     'rtBlend0': {'srcBlend': {'val': 5.0, 'name': '<noninit>'}, 'destBlend': {'val': 0.0, 'name': '_DstBlend'}},
                                     'culling': {'val': 0.0, 'name': '_CullMode'}, 'm_Tags': {'tags': []}}}]}]}}
        shader = layers.read_shader(tree)
        self.assertEqual((shader.name, shader.src, shader.dst, shader.cull, shader.queue), (tree['m_ParsedForm']['m_Name'], 5.0, '_DstBlend', '_CullMode', 3001))
        self.assertEqual(shader.defaults['_DstBlend'], 10.0)
        self.assertEqual(layers.queue_of('Geometry-10'), 1990)
        table = layers.shader_table('CAB-x', [(9, 'Shader', lambda: tree), (10, 'Texture2D', lambda: {})])
        self.assertEqual(list(table), [('CAB-x', 9)])


def read_material(tree):
    scene = Scene()
    tex = scene.add('Texture2D', {'m_Name': 't', 'm_Width': 512, 'm_Height': 256})
    other = scene.add('Texture2D', {'m_Name': 'noise', 'm_Width': 64, 'm_Height': 64})
    for env in tree['m_SavedProperties']['m_TexEnvs']:
        if env[1]['m_Texture'].get('m_FileID') == 0:
            env[1]['m_Texture'] = ref(tex if env[0] == '_MainTex' else other)
    return layers.read_material(tree, read=scene.read, external_of=external, shaders=SHADERS)


class Looks(unittest.TestCase):
    def test_anchor_adds_both_distortions_scaled_by_tiling(self):
        # Two distortions at once: their sum moves the texture, so a slight first one does not pass a strong second.
        both = material_tree(ANCHOR, 1, floats=[('_IntensityU', 0.05), ('_ToggleUseDisturb2', 1.0), ('_IntensityU_02', 0.5)],
                             extra_textures=[('_DisturTex', 2), ('_DisturTex_02', 2)])
        with self.assertRaisesRegex(layers.LayerError, r'flow distortion \(up to 0.55 UV, 281.6 texels\)'):
            layers.look(read_material(both))
        # The second is off: only the first counts; its texels follow the texture's tiling (2x).
        one = material_tree(ANCHOR, 1, floats=[('_IntensityU', 0.02), ('_IntensityU_02', 0.5)], extra_textures=[('_DisturTex', 2), ('_DisturTex_02', 2)],
                            st=((2, 1), (0, 0)))
        slight = layers.look(read_material(one))
        self.assertEqual(slight.approximated, 'flow distortion (up to 0.02 UV, 20.5 texels) drawn without it')
        # Drawn at the wobble's centre (texture 0.5 at anchor 0.5): no shift. Without a distortion texture the
        # anchor's constant shift (-anchor * intensity) stays.
        self.assertEqual(slight.st[2], 0.0)
        constant = layers.look(read_material(material_tree(ANCHOR, 1, floats=[('_IntensityU', 0.02)])))
        self.assertIsNone(constant.approximated)
        self.assertAlmostEqual(constant.st[2], -0.01)

    def test_erase_masks_order_like_the_layers_they_cover(self):
        # (sorting layer, order, queue, -z, hierarchy): a mask nearer the camera than a layer it precedes in the
        # hierarchy is drawn after it, so it covers it; one farther away covers nothing.
        omitted = []
        exporter = type('E', (), {})()
        exporter.layer_bounds = lambda layer: (0, 0, 1, 1)
        exporter.omit = lambda kind, name, reason: omitted.append(name)
        layer = {'layer': {'name': 'sky'}, 'sort': (0, 0, 3000, -1.0, 5)}
        exporter.masks = [{'name': 'near', 'sort': (0, 0, 3000, -0.5, 2), 'bounds': None}]
        self.assertEqual(layers._Exporter.unmasked(exporter, [layer]), [])
        self.assertEqual(omitted, ['sky'])
        omitted.clear()
        exporter.masks = [{'name': 'far', 'sort': (0, 0, 3000, -2.0, 9), 'bounds': None}]
        self.assertEqual(layers._Exporter.unmasked(exporter, [layer]), [layer])
        self.assertEqual(omitted, [])

    def test_plain_particle_shaders(self):
        look = layers.look(read_material(material_tree(ALPHA_BLEND, 1, colours=[('_TintColor', (0.5, 0.25, 1, 0.5))], st=((2, 1), (0.5, 0)))))
        self.assertEqual((look.blend, look.color, look.st, look.color_property), ('alpha', [0.5, 0.25, 1, 0.5], [2, 1, 0.5, 0], '_TintColor'))
        self.assertEqual(layers.look(read_material(material_tree(ADDITIVE, 1))).blend, 'add')
        with self.assertRaisesRegex(layers.LayerError, 'sprite sheet'):
            layers.look(read_material(material_tree(ALPHA_BLEND, 1, keywords='HG_SPRITE_SHEET')))

    def test_uber_shaders_count_only_when_their_effect_changes_nothing(self):
        # No distortion texture: plain, blended by the material's _DstBlend, scrolled by its speeds.
        plain = layers.look(read_material(material_tree(DISTURB, 1, floats=[('_DstBlend', 1.0), ('_Opacity', 0.5), ('_MainUSpeed', 0.1)],
                                                        colours=[('_MainColor', (1, 1, 1, 1))])))
        self.assertEqual((plain.blend, plain.alpha_scale, plain.opacity, plain.scroll), ('add', 1.0, 0.5, [0.1, 0.0]))
        # The material's own opacity is apart, so an animated one that fades in from 0 can replace it.
        faded = layers.look(read_material(material_tree(DISTURB, 1, floats=[('_Opacity', 0.0)])))
        self.assertEqual((faded.alpha_scale, faded.opacity), (1.0, 0.0))
        # A slight flow (a few texels) is drawn without it, and says so; a strong one is left out.
        slight = layers.look(read_material(material_tree(DISTURB, 1, floats=[('_IntensityU', 0.01)], extra_textures=[('_DisturbTex', 2)])))
        self.assertEqual(slight.approximated, 'flow distortion (up to 0.01 UV, 5.1 texels) drawn without it')
        self.assertIsNone(plain.approximated)
        strong = material_tree(DISTURB, 1, floats=[('_IntensityU', 0.2)], extra_textures=[('_DisturbTex', 2)])
        with self.assertRaisesRegex(layers.LayerError, r'flow distortion \(up to 0.2 UV, 102.4 texels\)'):
            layers.look(read_material(strong))
        self.assertTrue(layers.slight_flow({'uv': 0.06, 'texels': 40.0}))
        for flow in ({'uv': 0.07, 'texels': 10.0}, {'uv': 0.01, 'texels': 41.0}, {'uv': 0.01, 'texels': 0.0}):
            self.assertFalse(layers.slight_flow(flow), flow)
        with self.assertRaisesRegex(layers.LayerError, 'dissolve'):
            layers.look(read_material(material_tree(DISTURB, 1, floats=[('_Amount', 0.3)], extra_textures=[('_DissolveTex', 2)])))
        # Dissolve without a texture is a constant factor; amount 0 is none.
        self.assertEqual(layers.look(read_material(material_tree(DISSOLVE_ADD, 1, floats=[('_Amount', 0.0)]))).alpha_scale, 1.0)
        with self.assertRaisesRegex(layers.LayerError, 'dissolve'):
            layers.look(read_material(material_tree(DISSOLVE_ADD, 1, extra_textures=[('_DissolveTex', 2)])))
        with self.assertRaisesRegex(layers.LayerError, 'UV rotation'):
            layers.look(read_material(material_tree(DISTURB, 1, keywords='_HG_UV_ROTATION')))

    def test_unknown_or_missing_shaders_are_reasons(self):
        tree = material_tree(ALPHA_BLEND, 1)
        tree['m_Shader'] = {'m_FileID': 3, 'm_PathID': 99}
        with self.assertRaisesRegex(layers.LayerError, rf'shader not found \({SHADERS_CAB}:99\)'):
            layers.look(read_material(tree))


class TriggerStates(unittest.TestCase):
    def test_a_trigger_leads_from_the_default_state(self):
        def state(clip, speed=1.0, transitions=()):
            return {'data': {'m_Speed': speed, 'm_BlendTreeConstantArray': [{'data': {'m_NodeArray': [{'data': {'m_ClipID': clip}}]}}],
                             'm_TransitionConstantArray': [{'data': {'m_DestinationState': d, 'm_ConditionConstantArray': [
                                 {'data': {'m_ConditionMode': 1, 'm_EventID': e}}]}} for d, e in transitions]}}
        controller = {'m_TOS': [(11, 'OnIdle'), (12, 'OnInteract'), (13, 'OnSpecial')],
                      'm_Controller': {'m_LayerArray': [{'data': {'m_StateMachineIndex': 0}}],
                                       'm_Values': {'data': {'m_ValueArray': [{'m_ID': i, 'm_Type': 9} for i in (11, 12, 13)]}},
                                       'm_StateMachineArray': [{'data': {'m_DefaultState': 0, 'm_AnyStateTransitionConstantArray': [],
                                                                         'm_StateConstantArray': [state(0, 1.0, [(1, 12), (2, 13)]), state(2, 2.0), state(1)]}}]}}
        self.assertEqual(layers.trigger_states(controller, 3), {None: ([0], 1.0), 'Interact': ([2], 2.0), 'Special': ([1], 1.0)})
        self.assertEqual(layers.trigger_states({}, 3), {})


class Export(unittest.TestCase):
    def test_a_mirrored_static_backdrop_in_skeleton_units(self):
        p = Prefab()
        effects_go, effects = p.game_object('General Effects', p.root)
        # Turned half round about y: a mirror. Quad 4 x 2 units at (1, 2).
        p.quad('bg', effects, material_tree(ALPHA_BLEND, p.texture, colours=[('_TintColor', (0.5, 0.5, 0.5, 0.25))]), -1,
               position=(1, 2, 0), rotation=(0, 1, 0, 0), scale=(4, 2, 1))
        result = p.export()
        bg = layer_named(result, 'bg')
        self.assertEqual(bg['name'], 'bg')
        # (-0.5, -0.5) -> x mirrored: (2, -1) + (1, 2) = (3, 1) units = (300, 100) skeleton units.
        self.assertEqual(bg['vertices'][:2], [300.0, 100.0])
        self.assertEqual(bg['vertices'][2:4], [-100.0, 100.0])
        self.assertEqual(bg['uvs'][:4], [0.0, 1.0, 1.0, 1.0], 'v is flipped to image space')
        self.assertEqual(bg['color'], [1.0, 1.0, 1.0, 0.5], 'the tint counts double')
        self.assertEqual((bg['follow'], bg['animation'], bg['only'], bg['delay']), (None, None, None, 0.0))
        # One texel of the 4 x 4 texture shows: the frame fits it.
        self.assertEqual(result.document['textures'], [{'file': 'layer0.webp', 'width': 4, 'height': 4, 'wrap': ['clamp', 'repeat'],
                                                        'opaque': [0.25, 0.25, 0.5, 0.5]}])
        self.assertIsNone(result.document['bounds'])
        self.assertEqual(result.document['omitted']['holders'], 1)

    def test_draw_order_follows_sorting_order_around_the_skeleton_parts(self):
        p = Prefab()
        _, effects = p.game_object('General Effects', p.root)
        p.quad('front', effects, material_tree(ALPHA_BLEND, p.texture), 20)
        p.quad('between', effects, material_tree(ADDITIVE, p.texture), 5)
        p.quad('back', effects, material_tree(ALPHA_BLEND, p.texture), -1)
        p.quad('near', effects, material_tree(ALPHA_BLEND, p.texture), 5, position=(0, 0, -1))
        result = p.export()
        names = [e if isinstance(e, int) else e['name'] for e in entries(result)]
        # Same order: farther first (Unity's camera looks along +z).
        self.assertEqual(names, ['back', 0, 'between', 'near', 1, 'front'])
        self.assertEqual(result.document['separators'], ['slotB'], 'a separator the skeleton lacks is skipped')

    def test_an_animated_layer_samples_its_clip(self):
        p = Prefab()
        _, effects = p.game_object('General Effects', p.root)
        go, tr = p.quad('glow', effects, material_tree(ADDITIVE, p.texture, colours=[('_TintColor', (0.5, 0.5, 0.5, 0.5))]), 5)
        alpha = ec.crc('_TintColor') & 0x0FFFFFFF | (7 << 28)
        # Curves go to bindings in order: alpha (streamed), position x (streamed), y and z (constant).
        bindings = [{'path': 0, 'typeID': ec.RENDERER, 'attribute': alpha},
                    {'path': 0, 'typeID': ec.TRANSFORM, 'attribute': ec.POSITION}]
        clip = p.clip('glow_idle', bindings, [(-3.0e38, [(0, (0, 0, 0, 0.5)), (1, (0, 0, 0, 0))]),
                                              (0.0, [(0, linear(0.5, 0, 0, 1)), (1, linear(0, 1, 0, 1))]),
                                              (1.0, [(0, (0, 0, 0, 0)), (1, (0, 0, 0, 1))]), (float('inf'), [])],
                      constants=(0.0, 0.0), stop=1.0)
        p.objects[clip][1]['m_MuscleClip']['m_LoopTime'] = 1
        p.animate(go, clip)
        layer = layer_named(p.export(), 'glow')
        animation = layer['animation']
        self.assertEqual((animation['length'], animation['loop'], animation['loopFrom']), (1.0, True, 0.0))
        self.assertIsNone(layer['color'])
        self.assertEqual(layer['vertices'][:2], [-0.5, -0.5], 'local units: the frames place them')
        first, end, last = animation['frames']
        self.assertEqual(first[:12], [0.0, 100.0, 0.0, 0.0, 100.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        # A straight line keeps only its ends: the last sample before the loop wraps, and the wrap.
        self.assertEqual((end[0], end[5], end[10]), (0.9667, 96.67, 0.0333))
        self.assertEqual(last[1:], first[1:])

    def test_bone_followers_action_groups_and_delays(self):
        p = Prefab()
        follower_go, follower = p.game_object('Hand', p.root, position=(3, 0, 0))
        p.attach(follower_go, p.add('MonoBehaviour', {'m_Enabled': 1, 'skeletonRenderer': ref(p.skeleton), 'boneName': 'hand', 'followXYPosition': 1,
                                                      'followZPosition': 1, 'followBoneRotation': 1, 'followLocalScale': 0}))
        p.quad('halo', follower, material_tree(ALPHA_BLEND, p.texture), 2, scale=(2, 2, 1))
        group_go, group = p.game_object('Interact Only Effects', p.root, active=0)
        p.controller['_particles'].append({'action': 3, 'particle': ref(group_go)})
        clone_go, clone = p.game_object('interact_01(Clone)', group)
        p.attach(clone_go, p.add('MonoBehaviour', {'m_Enabled': 1, '_delayTime': 1.5}))
        p.quad('spark', clone, material_tree(ADDITIVE, p.texture), 3)
        found = {e['name']: e for e in entries(p.export()) if isinstance(e, dict)}
        halo = found['halo']
        self.assertEqual(halo['follow'], {'bone': 'hand', 'xy': True, 'rotation': True, 'localScale': False, 'mirrored': False,
                                          'parent': [100.0, 0.0, 0.0, 100.0], 'position': [300.0, 0.0], 'angle': 0.0})
        self.assertEqual(halo['vertices'][:2], [-1.0, -1.0], "the follower's own units")
        spark = found['spark']
        self.assertEqual((spark['only'], spark['delay']), ('Interact', 1.5), 'the group is switched with Interact; its own flag does not hide it')

    def test_an_unreadable_renderer_leaves_out_only_itself(self):
        # UnityPy raises ValueError on a line or point mesh, and missing fields raise KeyError: that renderer is
        # listed under omitted.other, and the model (with its other layers) is still written.
        p = Prefab()
        _, effects = p.game_object('General Effects', p.root)
        p.quad('good', effects, material_tree(ALPHA_BLEND, p.texture))
        p.quad('bad', effects, {**material_tree(ALPHA_BLEND, p.texture), 'm_Name': 'broken'})
        real = layers.look

        def look(material):
            if material.name == 'broken':
                raise ValueError('Unsupported topology: lines')
            return real(material)
        original, layers.look = layers.look, look
        try:
            result = p.export()
        finally:
            layers.look = original
        self.assertEqual([e['name'] for e in result.document['omitted']['other']], ['bad'])
        self.assertIn('unreadable (ValueError: Unsupported topology: lines)', result.document['omitted']['other'][0]['reason'])
        self.assertEqual(layer_named(result, 'good')['name'], 'good')

    def test_what_is_left_out_is_recorded_with_its_reason(self):
        p = Prefab()
        _, effects = p.game_object('General Effects', p.root)
        noise = p.add('Texture2D', {'m_Name': 'noise', 'm_Width': 64, 'm_Height': 64})
        p.quad('cloud', effects, material_tree(DISTURB, p.texture, floats=[('_IntensityV', 0.3)], extra_textures=[('_DisturbTex', noise)]))
        p.quad('mist', effects, material_tree(DISTURB, p.texture, floats=[('_IntensityV', 0.02)], extra_textures=[('_DisturbTex', noise)]))
        p.quad('shared', effects, material_tree(ALPHA_BLEND, p.texture, external_texture=True))
        p.quad('off', effects, material_tree(ALPHA_BLEND, p.texture), active=0)
        p.quad('times', effects, {**material_tree(ALPHA_BLEND, p.texture), 'm_Shader': {'m_FileID': 3, 'm_PathID': 98}})
        fx_go, _ = p.game_object('fx', effects)
        p.attach(fx_go, p.add('ParticleSystem', {}))
        p.renderer(fx_go, [], kind='ParticleSystemRenderer')
        trail_go, _ = p.game_object('trail', effects)
        p.renderer(trail_go, [], kind='TrailRenderer')
        rotating_go, rotating = p.quad('rotating', effects, material_tree(ALPHA_BLEND, p.texture))
        p.attach(rotating_go, p.add('MonoBehaviour', {'m_Enabled': 1, '_rotateTex1': 1, '_angle2': 0}))
        p.mesh_id = p.add('Mesh', {})
        p.meshes[p.mesh_id] = {'vertices': [(0, 0, 0), (1, 0, 0), (0, 1, 0)], 'uv': [(0, 0), (1, 0), (0, 1)], 'colors': [(1, 1, 1, 0.5)] * 3,
                               'submeshes': [[0, 1, 2]]}
        p.quad('tri', effects, material_tree(ALPHA_BLEND, p.texture), mesh=p.mesh_id)
        result = p.export()
        omitted = result.document['omitted']
        self.assertEqual((omitted['particles'], omitted['trails'], omitted['hidden']), (1, 1, 1))
        self.assertEqual(omitted['custom'], [{'name': 'cloud', 'reason': 'flow distortion (up to 0.3 UV, 1.2 texels)'},
                                             {'name': 'times', 'reason': f'shader not found ({SHADERS_CAB}:98)'}])
        self.assertEqual(omitted['externalTexture'], [{'name': 'shared', 'reason': f'texture in {OTHER_CAB}'}])
        self.assertEqual(omitted['other'], [{'name': 'rotating', 'reason': 'UV rotation script'}])
        tri = layer_named(result, 'tri')
        self.assertEqual((tri['triangles'], tri['colors'][:4]), ([0, 1, 2], [1.0, 1.0, 1.0, 0.5]))
        self.assertEqual(layer_named(result, 'mist')['approximated'], 'flow distortion (up to 0.02 UV, 0.1 texels) drawn without it')
        self.assertIsNone(tri['approximated'])
        self.assertEqual(result.counts['layers'], 2)

    def test_layers_under_an_erase_mask_are_left_out(self):
        p = Prefab()
        _, effects = p.game_object('General Effects', p.root)
        # The mask paints over what was drawn before it, where it reaches: the sky under it, not the
        # far-away moon, not the glow drawn after it.
        p.quad('sky', effects, material_tree(ALPHA_BLEND, p.texture), -1, scale=(4, 4, 1))
        p.quad('moon', effects, material_tree(ALPHA_BLEND, p.texture), -1, position=(20, 0, 0))
        p.quad('cut', effects, material_tree(ERASE, p.texture), 50, position=(1, 1, 0))
        p.quad('glow', effects, material_tree(ADDITIVE, p.texture), 60)
        result = p.export()
        self.assertEqual([e['name'] for e in entries(result) if isinstance(e, dict)], ['moon', 'glow'])
        self.assertIn({'name': 'sky', 'reason': 'under the mask cut (Erase), which is not drawn'}, result.document['omitted']['other'])
        self.assertIn({'name': 'cut', 'reason': 'shader Torappu/Particles-L2D/Mask/Erase'}, result.document['omitted']['custom'])
        self.assertEqual(result.counts['layers'], 2)

    def test_constant_clips_make_a_static_layer_and_clips_that_never_show_it_hide_it(self):
        p = Prefab()
        _, effects = p.game_object('General Effects', p.root)
        go, _ = p.quad('still', effects, material_tree(ALPHA_BLEND, p.texture))
        clip = p.clip('still_idle', [{'path': 0, 'typeID': ec.TRANSFORM, 'attribute': ec.POSITION}], [(float('inf'), [])], constants=(1.0, 0.0, 0.0), stop=0.0)
        p.animate(go, clip)
        hidden_go, _ = p.quad('never', effects, material_tree(ALPHA_BLEND, p.texture))
        off = p.clip('never_idle', [{'path': 0, 'typeID': ec.GAMEOBJECT, 'attribute': ec.IS_ACTIVE}], [(float('inf'), [])], constants=(0.0,), stop=2.0)
        p.animate(hidden_go, off)
        result = p.export()
        still = layer_named(result, 'still')
        self.assertIsNone(still['animation'])
        self.assertEqual(still['vertices'][:2], [50.0, -50.0], 'at its clip value: x = 1 unit')
        self.assertFalse(any(isinstance(e, dict) and e['name'] == 'never' for e in entries(result)))
        self.assertEqual(result.document['omitted']['hidden'], 1)


if __name__ == '__main__':
    unittest.main()
