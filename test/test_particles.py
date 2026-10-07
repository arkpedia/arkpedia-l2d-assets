"""Tests for scripts/particles.py on synthetic illustration prefabs, and the round trip through the reference
simulator: every system exported must simulate from its layerParticles.json record exactly as from its
typetrees (scripts/tests/particle_oracle.py, from_export against from_trees).

Run: python -m unittest discover -s test -p 'test_*.py'
"""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts' / 'tests'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import entrance_camera as ec  # noqa: E402
import layers  # noqa: E402
import particle_oracle as oracle  # noqa: E402
import particles  # noqa: E402
from test_camera import linear, ref  # noqa: E402
from test_layers import ADDITIVE, ALPHA_BLEND, DISTURB, ERASE, Prefab, colour_binding, material_tree  # noqa: E402
from test_particle_oracle import burst, curve, gradient, key, make_ps, mmc, mmg, multi  # noqa: E402

GROUP_INTERACT = 3


def renderer(**fields):
    tree = {'m_Enabled': 1, 'm_SortingLayerID': 0, 'm_SortingOrder': 0, 'm_RenderMode': 0, 'm_RenderAlignment': 0, 'm_SortMode': 0,
            'm_Pivot': {'x': 0, 'y': 0, 'z': 0}, 'm_Flip': {'x': 0, 'y': 0, 'z': 0}, 'm_MinParticleSize': 0.0, 'm_MaxParticleSize': 0.5,
            'm_LengthScale': 2.0, 'm_VelocityScale': 0.0, 'm_CameraVelocityScale': 0.0, 'm_SortingFudge': 0.0, 'm_UseCustomVertexStreams': 0,
            'm_VertexStreams': [0, 1, 3, 4], 'm_AllowRoll': 1, 'm_FreeformStretching': 0, 'm_RotateWithStretchDirection': 1,
            'm_Mesh': {'m_FileID': 0, 'm_PathID': 0}, 'm_Mesh1': {'m_FileID': 0, 'm_PathID': 0}, 'm_Mesh2': {'m_FileID': 0, 'm_PathID': 0},
            'm_Mesh3': {'m_FileID': 0, 'm_PathID': 0}, 'm_MeshDistribution': 0, 'm_MaskInteraction': 0}
    tree.update(fields)
    return tree


def unity(tree):
    """A typetree as Unity serializes it: every float a float32 (the export writes float32 and reads them back)."""
    if isinstance(tree, dict):
        return {k: unity(v) for k, v in tree.items()}
    if isinstance(tree, list):
        return [unity(v) for v in tree]
    if isinstance(tree, float):
        return oracle.f32(tree) if math.isfinite(tree) else tree
    return tree


def emitting(**kw):
    """A ParticleSystem typetree that emits 10 particles a second (test_particle_oracle.make_ps)."""
    return make_ps(**{'EmissionModule__rateOverTime': mmc(0, 10.0), 'lengthInSec': 2.0, 'looping': True, **kw})


class ParticlePrefab(Prefab):
    """The layer tests' prefab with particle systems added under the root."""

    def system(self, name, ps, *, parent=None, material=None, position=(0, 0, 0), rotation=(0, 0, 0, 1), scale=(1, 1, 1), active=1, **fields):
        go, tr = self.game_object(name, parent or self.root, position, rotation, scale, active)
        ps = unity(copy.deepcopy(ps))
        ps['m_GameObject'] = ref(go)
        self.attach(go, self.add('ParticleSystem', ps))
        materials = [self.add('Material', material if material is not None else material_tree(ADDITIVE, self.texture))] if material is not False else []
        r = unity(renderer(**fields))
        r.update({'m_GameObject': ref(go), 'm_Materials': [ref(m) for m in materials]})
        self.attach(go, self.add('ParticleSystemRenderer', r))
        return go, tr

    def run(self):
        result = self.export(particles=True)
        return result, result.particles, result.document

    def ps_of(self, go):
        """The ParticleSystem typetree on a GameObject."""
        return next(tree for kind, tree in self.objects.values() if kind == 'ParticleSystem' and tree['m_GameObject']['m_PathID'] == go)

    def ps_id(self, go):
        return next(pid for pid, (kind, tree) in self.objects.items() if kind == 'ParticleSystem' and tree['m_GameObject']['m_PathID'] == go)

    def link(self, parent_go, child_go, kind=0, properties=0, probability=1.0):
        """A sub-emitter link from one system to another (SubModule.subEmitters)."""
        module = self.ps_of(parent_go).setdefault('SubModule', {'enabled': True, 'subEmitters': []})
        module['subEmitters'].append({'emitter': ref(self.ps_id(child_go)), 'type': kind, 'properties': properties, 'emitProbability': probability})

    def follower(self, name, parent, position=(0, 0, 0), local_scale=0):
        go, tr = self.game_object(name, parent, position)
        self.attach(go, self.add('MonoBehaviour', {'m_Enabled': 1, 'skeletonRenderer': ref(self.skeleton), 'boneName': 'hand', 'followXYPosition': 1,
                                                   'followBoneRotation': 1, 'followLocalScale': local_scale}))
        return go, tr

    def triggered(self, go, default_clip, interact_clip):
        """An Animator the illustration controller triggers: its default state plays default_clip, OnInteract
        leads to interact_clip."""
        def state(clip, transitions=()):
            return {'data': {'m_Speed': 1.0, 'm_BlendTreeConstantArray': [{'data': {'m_NodeArray': [{'data': {'m_ClipID': clip}}]}}],
                             'm_TransitionConstantArray': [{'data': {'m_DestinationState': d, 'm_ConditionConstantArray': [
                                 {'data': {'m_ConditionMode': 1, 'm_EventID': e}}]}} for d, e in transitions]}}
        controller = self.add('AnimatorController', {
            'm_AnimationClips': [ref(default_clip), ref(interact_clip)], 'm_TOS': [(12, 'OnInteract')],
            'm_Controller': {'m_LayerArray': [{'data': {'m_StateMachineIndex': 0}}], 'm_Values': {'data': {'m_ValueArray': [{'m_ID': 12, 'm_Type': 9}]}},
                             'm_StateMachineArray': [{'data': {'m_DefaultState': 0, 'm_AnyStateTransitionConstantArray': [],
                                                               'm_StateConstantArray': [state(0, [(1, 12)]), state(1)]}}]}})
        animator = self.add('Animator', {'m_Controller': ref(controller), 'm_Enabled': 1})
        self.attach(go, animator)
        self.controller['_animators'].append(ref(animator))


INF = float('inf')
START = -3.0e38


def column(timeline, name, frame):
    """A frame's values of one named column (a number, or a list for wider columns)."""
    at = 0
    for c in timeline['columns']:
        n = particles.COLUMN_WIDTHS.get(c) or layers.effects.width(c)
        if c == name:
            return frame[at] if n == 1 else frame[at:at + n]
        at += n
    raise KeyError(name)


def reasons(document):
    return {item['name']: item['reason'] for item in document['omitted']['particleReasons']}


def records(sim):
    return [[*r['position'], *r['velocity'], *r['size'], *r['rotation'], *r['color'], *r['custom1'], *r['custom2'], *r['uv'], r['age01'],
             -1 if r['frame'] is None else r['frame']] for r in sim.render_records()]


class Export(unittest.TestCase):
    def test_a_static_system_with_unity_defaults_writes_almost_nothing(self):
        p = ParticlePrefab()
        p.system('dust', emitting())
        result, doc, layers_doc = p.run()
        self.assertEqual(len(doc['systems']), 1)
        s = doc['systems'][0]
        self.assertEqual({k: s[k] for k in ('name', 'requires', 'only', 'delay', 'active', 'follow', 'child')},
                         {'name': 'dust', 'requires': [], 'only': None, 'delay': 0, 'active': True, 'follow': None, 'child': False})
        # Duration 2 and a 10/s rate (the default); looping and the billboard are Unity's defaults, dropped. Start
        # values: make_ps's lifetime 100 and speed 0 are not the defaults (5 and 5).
        self.assertEqual(s['clock'], {'duration': 2, 'maxAlive': 1000})
        self.assertEqual(s['emission'], {})
        self.assertEqual(s['start'], {'lifetime': 100, 'speed': 0})
        self.assertEqual(s['render'], {'mode': 'billboard'})
        self.assertEqual(s['emitter'], {'matrix': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]})
        # Sorting order 0, after part 0 (same order and queue, later in the hierarchy) and before part 1 (order 10).
        self.assertEqual(layers_doc['draw'], [{'part': 0}, {'particles': [0]}, {'part': 1}])
        self.assertIsNone(layers_doc['particles'])  # scripts/sync.py fills the pointer when it writes the file
        self.assertEqual((layers_doc['omitted']['particles'], layers_doc['omitted']['particleReasons']), (0, []))
        # The material: plain additive, the texture x 2 x tint, Unity's v-up tiling applied in the fragment.
        material = doc['materials'][s['material']]
        self.assertEqual((material['blend'], material['color'], material['custom'], material['cull']), ('add', [1, 1, 1, 1], None, 0))
        self.assertEqual(material['shader']['main'], {'st': [1.0, 1.0, 0.0, 0.0], 'speed': [0.0, 0.0], 'scroll': [0.0, 0.0], 'fract': False})
        self.assertEqual(doc['textures'][0]['file'], 'layer0.webp')
        self.assertEqual(doc['textures'][0]['phase'], 'idle')

    def test_without_particles_layers_json_is_as_before(self):
        p = ParticlePrefab()
        p.system('dust', emitting())
        p.system('glow', emitting(), parent=p.root)
        before = p.export(particles=False)
        self.assertIsNone(before.particles)
        self.assertNotIn('particles', before.document)
        self.assertEqual(before.document['omitted']['particles'], 2)
        self.assertNotIn('particleReasons', before.document['omitted'])

    def test_the_emitter_matrix_follows_the_scaling_mode(self):
        p = ParticlePrefab()
        parent_go, parent = p.game_object('scaled', p.root, (1, 2, 0), (0, 0, 0, 1), (2, 2, 2))
        p.system('local', emitting(scalingMode=1), parent=parent, position=(1, 0, 0), scale=(0.5, 0.5, 0.5))
        p.system('hierarchy', emitting(scalingMode=0), parent=parent, position=(1, 0, 0), scale=(0.5, 0.5, 0.5))
        p.system('shape', emitting(scalingMode=2), parent=parent, position=(1, 0, 0), scale=(0.5, 0.5, 0.5))
        _, doc, _ = p.run()
        by_name = {s['name']: s['emitter'] for s in doc['systems']}
        # Local: the world position (1 + 2 x 1, 2) and rotation with its own scale only.
        self.assertEqual(by_name['scaled/local'], {'matrix': [0.5, 0, 0, 3, 0, 0.5, 0, 2, 0, 0, 0.5, 0], 'rotation': [0, 0, 0, 1], 'scale': [0.5, 0.5, 0.5]})
        # Hierarchy: the whole chain, and the lossy scale on sizes.
        self.assertEqual(by_name['scaled/hierarchy'], {'matrix': [1, 0, 0, 3, 0, 1, 0, 2, 0, 0, 1, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]})
        # Shape: the chain moves start positions only; sizes are not scaled.
        self.assertEqual(by_name['scaled/shape'], {'matrix': [1, 0, 0, 3, 0, 1, 0, 2, 0, 0, 1, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]})
        self.assertEqual({s['name']: s['clock'].get('scaling', 'local') for s in doc['systems']},
                         {'scaled/local': 'local', 'scaled/hierarchy': 'hierarchy', 'scaled/shape': 'shape'})

    def test_reasons_for_what_is_left_out(self):
        p = ParticlePrefab()
        p.system('off', emitting(), m_Enabled=0)
        p.system('none', emitting(), m_RenderMode=5)
        p.system('nomaterial', emitting(), material=False)
        p.system('inactive', emitting(), active=0)
        p.system('silent', make_ps(EmissionModule__rateOverTime=mmc(0, 0.0)))
        p.system('collides', emitting(CollisionModule={'enabled': True}))
        p.system('layer', emitting(), m_SortingLayerID=7)
        p.system('grab', emitting(), material=material_tree(DISTURB, p.texture, keywords='_HGCUSTOMVERTEXSTREAM_ON'))
        _, doc, layers_doc = p.run()
        self.assertIsNone(doc)  # nothing left to draw
        self.assertEqual(reasons(layers_doc), {
            'off': 'renderer off', 'none': 'render mode None', 'nomaterial': 'no material', 'inactive': 'never active', 'silent': 'emits nothing',
            'collides': 'Collision module', 'layer': 'sorting layer 7', 'grab': "custom vertex stream keyword without the renderer's streams"})
        self.assertEqual(layers_doc['omitted']['particles'], 8)
        self.assertTrue(set(particles.NOT_DRAWN) >= {'renderer off', 'render mode None', 'no material', 'never active', 'emits nothing'})

    def test_systems_sharing_a_path_are_told_apart(self):
        p = ParticlePrefab()
        p.system('spark', emitting())
        p.system('spark', emitting(), m_Enabled=0)
        p.system('spark', emitting())
        _, doc, layers_doc = p.run()
        self.assertEqual([s['name'] for s in doc['systems']], ['spark', 'spark #3'])
        self.assertEqual(reasons(layers_doc), {'spark #2': 'renderer off'})

    def test_runs_form_where_systems_sort_next_to_each_other(self):
        p = ParticlePrefab()
        p.system('a', emitting(), m_SortingOrder=5)
        p.system('b', emitting(), m_SortingOrder=5)
        p.quad('between', p.root, material_tree(ALPHA_BLEND, p.texture), 6)
        p.system('c', emitting(), m_SortingOrder=7)
        p.system('first', emitting(), m_SortingOrder=-3)
        # A fudge moves a system back (a higher one) or forward among equals.
        p.system('fudged', emitting(), m_SortingOrder=7, m_SortingFudge=-100.0)
        _, doc, layers_doc = p.run()
        self.assertEqual([s['name'] for s in doc['systems']], ['first', 'a', 'b', 'c', 'fudged'])
        draw = layers_doc['draw']
        self.assertEqual([next(iter(e)) for e in draw], ['particles', 'part', 'particles', 'layer', 'particles', 'part'])
        self.assertEqual([e['particles'] for e in draw if 'particles' in e], [[0], [1, 2], [3, 4]])

    def test_an_opaque_queue_draws_before_everything_and_a_later_mask_leaves_a_system_out(self):
        p = ParticlePrefab()
        opaque = material_tree(ALPHA_BLEND, p.texture)
        opaque['m_CustomRenderQueue'] = 2000
        p.system('opaque', emitting(), material=opaque, m_SortingOrder=100)
        p.system('late', emitting(), m_SortingOrder=100)
        _, doc, layers_doc = p.run()
        # Unity's opaque pass runs before every transparent object, whatever its sorting order.
        self.assertEqual([s['name'] for s in doc['systems']], ['opaque', 'late'])
        self.assertEqual(layers_doc['draw'][0], {'particles': [0]})
        p.system('under', emitting(), m_SortingOrder=10)
        p.quad('cut', p.root, material_tree(ERASE, p.texture), 50)
        p.system('over', emitting(), m_SortingOrder=60)
        _, doc, layers_doc = p.run()
        # A system has no fixed box to test against the mask's: drawn before it (the opaque one too), it is left out.
        self.assertEqual([s['name'] for s in doc['systems']], ['over', 'late'])
        self.assertEqual(reasons(layers_doc), {'under': 'under the mask cut (Erase), which is not drawn',
                                               'opaque': 'under the mask cut (Erase), which is not drawn'})

    def test_custom_inputs_follow_the_renderer_streams(self):
        p = ParticlePrefab()
        tex2 = p.add('Texture2D', {'m_Name': 'noise', 'm_Width': 4, 'm_Height': 4, 'm_TextureSettings': {'m_WrapU': 0, 'm_WrapV': 0}})
        disturb = material_tree(DISTURB, p.texture, keywords='_HGCUSTOMVERTEXSTREAM_ON', extra_textures=[('_DissolveTex', tex2)])
        custom = {'enabled': True, 'mode0': 0, 'mode1': 1, 'vectorComponentCount1': 1,
                  'vector1_0': mmc(1, 1.0, 1.0, [key(0, 0, 1, 1), key(1, 1, 1, 1)]), 'vector1_1': mmc(), 'vector1_2': mmc(), 'vector1_3': mmc(),
                  'color0': mmg(), 'color1': mmg()}
        p.system('streams', emitting(CustomDataModule=custom), material=disturb, m_UseCustomVertexStreams=1, m_VertexStreams=[0, 1, 3, 4, 34, 38])
        p.system('uv2', emitting(CustomDataModule=custom), material=disturb, m_UseCustomVertexStreams=1, m_VertexStreams=[0, 1, 3, 4, 5, 34])
        _, doc, _ = p.run()
        inputs = {s['name']: doc['materials'][s['material']]['custom']['inputs'] for s in doc['systems']}
        self.assertEqual(inputs['streams'], ['c1.x', 'c1.y', 'c1.z', 'c1.w', 'c2.x', 'c2.y', 'c2.z', 'c2.w'])
        self.assertEqual(inputs['uv2'], ['uv2.x', 'uv2.y', 'c1.x', 'c1.y', 'c1.z', 'c1.w', 0, 0])
        # Custom2.x drives the dissolve amount per particle, so the dissolve exists although _Amount is 0.
        shader = doc['materials'][doc['systems'][0]['material']]['shader']
        self.assertEqual(len(shader['dissolve']), 1)
        # The same material on the other layout drives nothing through Custom2.x: no dissolve.
        self.assertEqual(doc['materials'][doc['systems'][1]['material']]['shader']['dissolve'], [])

    def test_particle_textures_number_on_after_the_layers_and_carry_their_phase(self):
        p = ParticlePrefab()
        p.quad('sky', p.root, material_tree(ALPHA_BLEND, p.texture))
        own = p.add('Texture2D', {'m_Name': 'spark', 'm_Width': 4, 'm_Height': 4, 'm_TextureSettings': {'m_WrapU': 1, 'm_WrapV': 1}})
        group, group_tr = p.game_object('Interact Only Effects', p.root)
        p.controller['_particles'].append({'particle': ref(group), 'action': GROUP_INTERACT})
        p.system('shared', emitting())
        p.system('spark', emitting(), parent=group_tr, material=material_tree(ADDITIVE, own))
        _, doc, layers_doc = p.run()
        self.assertEqual([t['file'] for t in layers_doc['textures']], ['layer0.webp'])
        self.assertEqual([(t['file'], t['phase']) for t in doc['textures']], [('layer1.webp', 'Interact')])
        by_name = {s['name']: s for s in doc['systems']}
        self.assertEqual(by_name['Interact Only Effects/spark']['only'], 'Interact')
        self.assertEqual(doc['materials'][by_name['shared']['material']]['texture'], 0)
        self.assertEqual(doc['materials'][by_name['Interact Only Effects/spark']['material']]['texture'], 1)

    def test_mesh_particles_and_mesh_shapes(self):
        p = ParticlePrefab()
        mesh = p.add('Mesh', {})
        p.meshes[mesh] = {'vertices': [(0, 0, 0), (1, 0, 0), (0, 2, 0), (1, 2, 0)], 'uv': [(0, 0), (1, 0), (0, 1), (1, 1)], 'colors': None,
                          'submeshes': [[0, 1, 2, 2, 1, 3]], 'normals': [(0, 0, -1)] * 4}
        shape = {'ShapeModule__enabled': True, 'ShapeModule__type': 6, 'ShapeModule__m_Mesh': ref(mesh), 'ShapeModule__placementMode': 2,
                 'ShapeModule__m_MeshSpawn': multi(0.0), 'ShapeModule__m_UseMeshColors': True, 'ShapeModule__m_MeshNormalOffset': 0.0}
        p.system('quads', emitting(), m_RenderMode=4, m_Mesh={'m_FileID': 1, 'm_PathID': layers.BUILTIN_QUAD})
        p.system('fromMesh', emitting(**shape))
        _, doc, _ = p.run()
        by_name = {s['name']: s for s in doc['systems']}
        self.assertEqual(doc['meshes'][by_name['quads']['render']['meshes'][0]], {'builtin': 'quad'})
        shaped = by_name['fromMesh']['shape']
        self.assertEqual((shaped['type'], shaped['placement']), ('mesh', 'triangle'))
        record = doc['meshes'][shaped['mesh']]
        self.assertEqual((record['areaCdf'], record['normals'][:3], record['triangles']), ([0.5, 1], [0, 0, -1], [0, 1, 2, 2, 1, 3]))

    def test_numbers_are_float32_shortest(self):
        self.assertEqual([particles.num(v) for v in (0.1, 2.0, -0.0, 0.30000001192092896, 1e-7)], [0.1, 2, 0, 0.3, 1e-07])
        with self.assertRaises(particles.Omit):
            particles.num(float('nan'))
        self.assertEqual(particles.curve(mmc(1, 2.0, 9.0, [key(0, 0, 0, math.inf), key(1, 1)])), ['c', 2, [0, 0, 0, None, 1, 1, 0, 0]])
        self.assertEqual(particles.curve({**mmc(1, 1.0, 1.0, [key(0, 0), key(1, 1)]), 'maxCurve': curve([key(0, 0), key(1, 1)], post=1)}),
                         ['c', 1, [0, 0, 0, 0, 1, 1, 0, 0], ['w', 'clamp', 'repeat']])
        g = gradient(colors=((0.0, (1, 0, 0)), (0.5, (0, 0, 1))), alphas=((0.0, 1.0), (1.0, 0.0)), mode=1)
        # Key times are time / 65535 as a float32 (32768 / 65535: 0.5000076 reads back to it).
        self.assertEqual(particles.colour_value(mmg(1, max_gradient=g)), ['g', {'c': [0, 1, 0, 0, 0.5000076, 0, 0, 1], 'a': [0, 1, 1, 0], 'fixed': True}])


def module_systems():
    """Systems that exercise every value mode and module the export writes."""
    lin = [key(0, 0, 1, 1), key(1, 1, 1, 1)]
    bump = [key(0, 0, 4, 4), key(0.5, 1, 0, 0), key(1, 0.2, -1, -1)]
    two_g = mmg(3, max_gradient=gradient(colors=((0.0, (1, 0.5, 0)), (0.7, (0, 1, 1))), alphas=((0.0, 0.0), (0.2, 1.0), (1.0, 0.0))),
                min_gradient=gradient(colors=((0.0, (0, 0, 1)), (1.0, (1, 1, 1))), mode=1))
    systems = {
        'curves': emitting(InitialModule__startLifetime=mmc(3, 1.5, 0.5), InitialModule__startSpeed=mmc(1, 3.0, 0.0, bump),
                           InitialModule__startSize=mmc(2, 0.4, 7.0, lin, bump), InitialModule__startColor=two_g,
                           InitialModule__gravityModifier=mmc(3, 0.3, -0.1), InitialModule__randomizeRotationDirection=0.5,
                           InitialModule__startRotation=mmc(3, 3.14, -1.0), EmissionModule__m_Bursts=[burst(0.25, 3, cycles=2, interval=0.4)],
                           ColorModule={'enabled': True, 'gradient': mmg(1, max_gradient=gradient(alphas=((0.0, 0.0), (0.5, 1.0), (1.0, 0.0))))},
                           SizeModule={'enabled': True, 'curve': mmc(1, 2.0, 1.0, bump), 'y': mmc(), 'z': mmc(), 'separateAxes': False}),
        '3d': emitting(InitialModule__size3D=True, InitialModule__startSizeY=mmc(0, 2.0), InitialModule__startSizeZ=mmc(3, 1.0, 3.0),
                       InitialModule__rotation3D=True, InitialModule__startRotationX=mmc(3, 0.0, 1.0), InitialModule__startRotationY=mmc(0, 0.5),
                       RotationModule={'enabled': True, 'x': mmc(0, 1.0), 'y': mmc(3, -1.0, 1.0), 'curve': mmc(1, 2.0, 0.0, lin), 'separateAxes': True},
                       SizeModule={'enabled': True, 'curve': mmc(1, 1.0, 0.0, lin), 'y': mmc(0, 2.0), 'z': mmc(1, 1.0, 0.0, bump), 'separateAxes': True}),
        'motion': emitting(moveWithTransform=1, InitialModule__startSpeed=mmc(0, 2.0),
                           VelocityModule={'enabled': True, 'x': mmc(1, 1.0, 0.0, bump), 'y': mmc(3, -1.0, 1.0), 'z': mmc(), 'orbitalX': mmc(),
                                           'orbitalY': mmc(), 'orbitalZ': mmc(0, 0.5), 'orbitalOffsetX': mmc(0, 0.1), 'orbitalOffsetY': mmc(),
                                           'orbitalOffsetZ': mmc(), 'radial': mmc(0, 0.2), 'speedModifier': mmc(0, 0.8), 'inWorldSpace': True},
                           ClampVelocityModule={'enabled': True, 'x': mmc(), 'y': mmc(), 'z': mmc(), 'magnitude': mmc(1, 1.0, 0.0, lin),
                                                'separateAxis': False, 'inWorldSpace': False, 'dampen': 0.2, 'drag': mmc()},
                           ForceModule={'enabled': True, 'x': mmc(0, 0.1), 'y': mmc(3, 0.5, 1.0), 'z': mmc(), 'inWorldSpace': False, 'randomizePerFrame': False},
                           InheritVelocityModule={'enabled': True, 'm_Mode': 0, 'm_Curve': mmc(0, 0.5)},
                           NoiseModule={'enabled': True, 'strength': mmc(0, 0.6), 'strengthY': mmc(), 'strengthZ': mmc(), 'separateAxes': False,
                                        'frequency': 0.3, 'damping': True, 'octaves': 2, 'octaveMultiplier': 0.5, 'octaveScale': 2.0, 'quality': 1,
                                        'scrollSpeed': mmc(0, 0.25), 'remapEnabled': False, 'positionAmount': mmc(0, 1.0), 'rotationAmount': mmc(),
                                        'sizeAmount': mmc()}),
        'limits': emitting(InitialModule__startSpeed=mmc(0, 6.0),
                           ClampVelocityModule={'enabled': True, 'x': mmc(0, 1.0), 'y': mmc(3, 0.5, 2.0), 'z': mmc(0, 1.0), 'magnitude': mmc(0, 9.0),
                                                'separateAxis': True, 'inWorldSpace': True, 'dampen': 0.5, 'drag': mmc()}),
        'sheet': emitting(UVModule={'enabled': True, 'mode': 0, 'timeMode': 0, 'tilesX': 4, 'tilesY': 2, 'animationType': 1, 'rowMode': 1, 'rowIndex': 0,
                                    'cycles': 2.0, 'frameOverTime': mmc(1, 0.9999, 0.0, lin), 'startFrame': mmc(3, 0.0, 0.5), 'uvChannelMask': -1,
                                    'flipU': 0.0, 'flipV': 0.0},
                          CustomDataModule={'enabled': True, 'mode0': 1, 'vectorComponentCount0': 3, 'vector0_0': mmc(1, 2.0, 0.0, bump),
                                            'vector0_1': mmc(3, 0.0, 1.0), 'vector0_2': mmc(0, 5.0), 'vector0_3': mmc(0, 9.0), 'mode1': 2,
                                            'vectorComponentCount1': 4, 'vector1_0': mmc(), 'vector1_1': mmc(), 'vector1_2': mmc(), 'vector1_3': mmc(),
                                            'color0': mmg(), 'color1': mmg(2, max_color=(1, 0, 0, 1), min_color=(0, 1, 0, 0.5))}),
        'prewarmed': make_ps(lengthInSec=10.0, looping=True, prewarm=True, EmissionModule__rateOverTime=mmc(0, 20.0),
                             InitialModule__startLifetime=mmc(3, 0.5, 1.5)),
        'capped': make_ps(lengthInSec=5.0, looping=True, prewarm=True, InitialModule__maxNumParticles=1, EmissionModule__rateOverTime=mmc(0, 4.0),
                          InitialModule__startLifetime=mmc(0, 0.6)),
        'delayed': make_ps(lengthInSec=1.0, looping=False, startDelay=mmc(3, 0.1, 0.3), autoRandomSeed=False, randomSeed=-334000898,
                           simulationSpeed=1.5, EmissionModule__rateOverTime=mmc(0, 30.0), InitialModule__startLifetime=mmc(0, 0.7)),
    }
    shapes = {
        'cone': {'type': 4, 'angle': 30.0, 'radius': multi(0.5), 'radiusThickness': 0.2, 'arc': multi(270.0, mode=2, spread=0.1)},
        'coneVolume': {'type': 8, 'angle': 10.0, 'length': 2.0, 'radius': multi(0.4)},
        'sphere': {'type': 0, 'radius': multi(2.0), 'radiusThickness': 0.0, 'randomDirectionAmount': 0.3, 'randomPositionAmount': 0.2},
        'hemisphere': {'type': 2, 'radius': multi(1.5), 'sphericalDirectionAmount': 0.5},
        'circle': {'type': 10, 'radius': multi(1.0), 'arc': multi(90.0, mode=1)},
        'box': {'type': 5, 'm_Scale': {'x': 4, 'y': 1, 'z': 0}, 'm_Rotation': {'x': -90, 'y': 0, 'z': 15}, 'm_Position': {'x': 0.5, 'y': 0, 'z': 0}},
        'boxShell': {'type': 15, 'boxThickness': {'x': 0.2, 'y': 0.1, 'z': 0}},
        'edge': {'type': 12, 'radius': multi(3.0, mode=3)},
        'donut': {'type': 17, 'radius': multi(2.0), 'donutRadius': 0.5, 'arc': multi(360.0, spread=0.25)},
        'rectangle': {'type': 18},
    }
    for name, fields in shapes.items():
        systems[f'shape-{name}'] = emitting(**{'ShapeModule__enabled': True, **{f'ShapeModule__{k}': v for k, v in fields.items()},
                                               'InitialModule__startSpeed': mmc(0, 1.0),
                                               'EmissionModule__m_Bursts': [burst(0.0, 8)]})
    return systems


class Moving(unittest.TestCase):
    """Bone followers, emitter timelines, and what a clip may drive."""

    def test_a_system_on_a_bone_follower_composes_as_unity_scales_it(self):
        p = ParticlePrefab()
        _, holder = p.game_object('holder', p.root, (5, 0, 0), (0, 0, 0, 1), (2, 2, 2))  # a scaled parent above the follower
        _, hand = p.follower('Hand', holder, (3, 0, 0))
        q90 = (0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4))
        p.system('local', emitting(scalingMode=1), parent=hand, position=(1, 0, 0), rotation=q90, scale=(0.5, 0.5, 0.5))
        p.system('hierarchy', emitting(scalingMode=0), parent=hand, position=(1, 0, 0), rotation=q90, scale=(0.5, 0.5, 0.5))
        _, doc, _ = p.run()
        by = {s['name']: s for s in doc['systems']}
        local, hierarchy = by['holder/Hand/local'], by['holder/Hand/hierarchy']
        self.assertEqual(local['follow'], {'bone': 'hand', 'xy': True, 'rotation': True, 'localScale': False, 'mirrored': False,
                                           'parent': [200.0, 0.0, 0.0, 200.0], 'position': [1100.0, 0.0], 'angle': 0.0})
        # Relative to the follower: 1 unit along its x, turned 90 degrees, half its size.
        for system in (hierarchy, local):
            self.assertTrue(all(abs(a - b) < 1e-6 for a, b in zip(system['emitter']['matrix'], [0, -0.5, 0, 1, 0.5, 0, 0, 0, 0, 0, 0.5, 0])), system['emitter'])
        self.assertEqual((local['emitter']['scale'], hierarchy['emitter']['scale']), ([0.5, 0.5, 0.5], [1, 1, 1]))
        # Composed with the follower's frame where Unity has it (x 11, scale 2), Hierarchy takes the parent's
        # scale and Local does not: Unity's matrices.
        frame = ([2, 0, 0, 11, 0, 2, 0, 0, 0, 0, 2, 0], (0.0, 0.0, 0.0, 1.0))
        h, lo = oracle.emitter_pose(hierarchy, 0.0, None, frame), oracle.emitter_pose(local, 0.0, None, frame)
        self.assertEqual((h.position, lo.position), ((13.0, 0.0, 0.0), (13.0, 0.0, 0.0)))
        for got, want in ((h.linear, (0, -1, 0, 1, 0, 0, 0, 0, 1)), (lo.linear, (0, -0.5, 0, 0.5, 0, 0, 0, 0, 0.5))):
            self.assertTrue(all(abs(a - b) < 1e-6 for a, b in zip(got, want)), got)

    def test_a_moving_switched_and_driven_system_has_a_timeline(self):
        p = ParticlePrefab()
        group_go, _ = p.game_object('fx', p.root)
        p.system('spark', emitting())
        go, _ = p.system('spark', emitting(), parent=p.objects[group_go][1]['m_Component'][0]['component']['m_PathID'])
        # The group's clip (1 s): x 0 -> 3, the spark off at 0.5167 s (between two 30 fps samples), its speed 1 -> 2
        # and its tint's alpha 0.5 -> 0. Bindings take the curves in order; the position's y and z are constants.
        bindings = [{'path': ec.crc('spark'), 'typeID': ec.GAMEOBJECT, 'attribute': ec.IS_ACTIVE},
                    {'path': ec.crc('spark'), 'typeID': ec.PARTICLE_SYSTEM, 'attribute': ec.crc('simulationSpeed')},
                    {'path': ec.crc('spark'), 'typeID': ec.PARTICLE_RENDERER, 'attribute': colour_binding('_TintColor', 3)},
                    {'path': 0, 'typeID': ec.TRANSFORM, 'attribute': ec.POSITION}]
        clip = p.clip('fx_idle', bindings, [(START, [(0, (0, 0, 0, 1)), (1, (0, 0, 0, 1)), (2, (0, 0, 0, 0.5)), (3, (0, 0, 0, 0))]),
                                            (0.0, [(0, (0, 0, 0, 1)), (1, linear(1, 2, 0, 1)), (2, linear(0.5, 0, 0, 1)), (3, linear(0, 3, 0, 1))]),
                                            (0.5167, [(0, (0, 0, 0, 0))]),
                                            (1.0, [(0, (0, 0, 0, 0)), (1, (0, 0, 0, 2)), (2, (0, 0, 0, 0)), (3, (0, 0, 0, 3))]), (INF, [])],
                      constants=(0.0, 0.0), stop=1.0)
        p.animate(group_go, clip)
        _, doc, _ = p.run()
        by = {s['name']: s for s in doc['systems']}
        self.assertIn('matrix', by['spark']['emitter'], 'a still emitter')
        timeline = by['fx/spark']['emitter']['timeline']
        self.assertEqual(timeline['columns'], ['t', 'matrix', 'rotation', 'scale', 'active', 'speed', 'tint'])
        self.assertEqual((timeline['length'], timeline['loop'], timeline['loopFrom']), (1, False, 0))
        frames = timeline['frames']
        # The switch is found between the samples, to 1e-4 s: on until 0.5167, off from there (stepped).
        switch = next(f[0] for f in frames if column(timeline, 'active', f) == 0)
        self.assertEqual(switch, 0.5167)
        self.assertEqual(column(timeline, 'active', [f for f in frames if f[0] < switch][-1]), 1)
        values = oracle.timeline_values(timeline, 0.5)
        self.assertAlmostEqual(values['matrix'][3], 1.5, places=4)  # x, root Unity units
        self.assertAlmostEqual(values['speed'], 1.5, places=4)
        self.assertAlmostEqual(values['tint'][3], 0.5, places=4)  # the material colour x 2
        self.assertEqual(oracle.timeline_values(timeline, 5.0)['matrix'][3], 3)  # it holds after its end
        self.assertTrue(by['fx/spark']['active'])

    def test_a_triggered_state_carries_its_own_timeline(self):
        p = ParticlePrefab()
        group_go, group = p.game_object('fx', p.root)
        p.system('lightning', emitting(), parent=group)
        off = [{'path': ec.crc('lightning'), 'typeID': ec.GAMEOBJECT, 'attribute': ec.IS_ACTIVE}]
        idle = p.clip('idle', off, [(START, [(0, (0, 0, 0, 1))]), (0.0, [(0, (0, 0, 0, 1))]), (INF, [])], stop=1.0)
        interact = p.clip('toInteract', off, [(START, [(0, (0, 0, 0, 0))]), (0.0, [(0, (0, 0, 0, 0))]), (13.0667, [(0, (0, 0, 0, 1))]), (INF, [])],
                          stop=13.5333)
        p.triggered(group_go, idle, interact)
        _, doc, _ = p.run()
        timeline = doc['systems'][0]['emitter']['timeline']
        self.assertEqual({column(timeline, 'active', f) for f in timeline['frames']}, {1})
        state = timeline['states']['Interact']
        self.assertEqual(state['length'], 13.5333)
        self.assertEqual([(f[0], column(timeline, 'active', f)) for f in state['frames'] if column(timeline, 'active', f) == 1][0], (13.0667, 1))

    def test_a_clip_that_only_switches_a_system_off_leaves_it_never_active(self):
        p = ParticlePrefab()
        group_go, group = p.game_object('fx', p.root)
        p.system('dust', emitting(), parent=group)
        p.animate(group_go, p.clip('off', [{'path': ec.crc('dust'), 'typeID': ec.GAMEOBJECT, 'attribute': ec.IS_ACTIVE}],
                                   [(START, [(0, (0, 0, 0, 0))]), (0.0, [(0, (0, 0, 0, 0))]), (INF, [])], stop=1.0))
        _, _, layers_doc = p.run()
        self.assertEqual(reasons(layers_doc), {'fx/dust': 'never active'})

    def test_driven_fields_have_columns_and_others_are_reasons(self):
        p = ParticlePrefab()
        for name in ('rate', 'quiet', 'speed'):
            go, _ = p.system(name, emitting())
        field = {'rate': 'EmissionModule.rateOverTime.scalar', 'quiet': 'NoiseModule.strength.scalar', 'speed': 'InitialModule.startSpeed.scalar'}
        for name, path in field.items():
            go = next(g for g, (kind, tree) in p.objects.items() if kind == 'GameObject' and tree['m_Name'] == name)
            p.animate(go, p.clip(name, [{'path': 0, 'typeID': ec.PARTICLE_SYSTEM, 'attribute': ec.crc(path)}],
                                 [(START, [(0, (0, 0, 0, 10))]), (0.0, [(0, linear(10, 20, 0, 1))]), (1.0, [(0, (0, 0, 0, 20))]), (INF, [])], stop=1.0))
        _, doc, layers_doc = p.run()
        by = {s['name']: s for s in doc['systems']}
        timeline = by['rate']['emitter']['timeline']
        self.assertEqual(timeline['columns'][5:], ['emission.rate'])
        self.assertEqual(oracle.timeline_values(timeline, 1.0)['emission.rate'], 20)
        self.assertIn('matrix', by['quiet']['emitter'], 'the noise module is off: its strength changes nothing')
        self.assertEqual(reasons(layers_doc), {'speed': 'driven by a clip: InitialModule.startSpeed.scalar'})

    def test_a_camera_shake_script_changes_nothing_a_particle_draws(self):
        p = ParticlePrefab()
        shaker_go, shaker = p.game_object('shaker', p.root)
        p.attach(shaker_go, p.add('MonoBehaviour', {'m_Enabled': 1, '_shakeTrigs': []}))
        p.system('spark', emitting(), parent=shaker)
        p.quad('glow', shaker, material_tree(ADDITIVE, p.texture))
        _, doc, layers_doc = p.run()
        self.assertEqual([s['name'] for s in doc['systems']], ['shaker/spark'])
        # The layers' own export does not change (it is not versioned with particles): the mesh is still left out.
        self.assertIn({'name': 'glow', 'reason': 'script (_shakeTrigs)'}, layers_doc['omitted']['other'])


class SubEmitters(unittest.TestCase):
    def test_a_family_is_exported_with_its_links_and_a_spawner_draws_nothing(self):
        p = ParticlePrefab()
        ctrl, ctrl_tr = p.system('ring_ctrl', emitting(), m_Enabled=0)  # its renderer is off: it only spawns
        fire, _ = p.system('fire', emitting(), parent=ctrl_tr, m_SortingOrder=3)
        spark, _ = p.system('spark', emitting(), parent=ctrl_tr, m_SortingOrder=-2)
        p.link(ctrl, fire)
        p.link(ctrl, spark, kind=2, probability=0.5)
        _, doc, layers_doc = p.run()
        names = [s['name'] for s in doc['systems']]
        self.assertEqual(names, ['ring_ctrl/spark', 'ring_ctrl', 'ring_ctrl/fire'])
        spawner = doc['systems'][1]
        self.assertEqual((spawner['render'], spawner['material'], spawner['requires'], spawner['child']), (None, None, ['sub'], False))
        self.assertEqual(spawner['sub'], [[2, 'birth', 1], [0, 'death', 0.5]])
        self.assertEqual([(s['child'], s['requires']) for s in doc['systems'][::2]], [(True, ['sub']), (True, ['sub'])])
        # The spawner has its index and no run; the children draw where they sort.
        self.assertEqual([e['particles'] for e in layers_doc['draw'] if 'particles' in e], [[0], [2]])

    def test_a_family_is_left_out_together(self):
        p = ParticlePrefab()
        parent, parent_tr = p.system('ctrl', emitting())
        child, _ = p.system('fire', emitting(CollisionModule={'enabled': True}), parent=parent_tr)
        p.link(parent, child)
        inherit, inherit_tr = p.system('inherits', emitting())
        heir, _ = p.system('heir', emitting(), parent=inherit_tr)
        p.link(inherit, heir, properties=10)
        _, doc, layers_doc = p.run()
        self.assertIsNone(doc)
        self.assertEqual(reasons(layers_doc), {'ctrl/fire': 'Collision module', 'ctrl': 'its sub-emitter ctrl/fire is left out',
                                               'inherits': "a sub-emitter that inherits its parent's size, lifetime",
                                               'inherits/heir': 'the systems it is a sub-emitter of are left out'})

    def test_a_child_that_draws_nothing_loses_its_link(self):
        p = ParticlePrefab()
        ctrl, ctrl_tr = p.system('ctrl', emitting())
        hidden, _ = p.system('hidden', emitting(), parent=ctrl_tr, active=0)
        p.link(ctrl, hidden)
        spawner, spawner_tr = p.system('spawner', emitting(), m_Enabled=0)
        dark, _ = p.system('dark', emitting(), parent=spawner_tr, m_Enabled=0)
        p.link(spawner, dark)
        _, doc, layers_doc = p.run()
        self.assertEqual([(s['name'], s['requires'], 'sub' in s) for s in doc['systems']], [('ctrl', [], False)])
        self.assertEqual(reasons(layers_doc), {'ctrl/hidden': 'never active', 'spawner/dark': 'renderer off', 'spawner': 'its sub-emitters draw nothing'})
        self.assertTrue(all(r in particles.NOT_DRAWN for r in reasons(layers_doc).values()))

    def test_a_parent_prewarms_long_enough_for_its_children(self):
        p = ParticlePrefab()
        parent, parent_tr = p.system('ctrl', make_ps(lengthInSec=10.0, looping=True, prewarm=True, EmissionModule__rateOverTime=mmc(0, 2.0),
                                                     InitialModule__startLifetime=mmc(0, 1.5)))
        child, _ = p.system('fire', emitting(InitialModule__startLifetime=mmc(3, 0.5, 2.0)), parent=parent_tr)
        p.link(parent, child)
        _, doc, _ = p.run()
        self.assertEqual(doc['systems'][0]['clock']['prewarmWindow'], 3.5)  # 1.5 + 2.0


class Trails(unittest.TestCase):
    def test_trails_are_written_as_data_under_their_capability(self):
        p = ParticlePrefab()
        trail = {'enabled': True, 'mode': 0, 'ratio': 1.0, 'lifetime': mmc(0, 0.5), 'minVertexDistance': 0.1, 'textureMode': 0, 'ribbonCount': 1,
                 'worldSpace': False, 'dieWithParticles': True, 'sizeAffectsWidth': True, 'sizeAffectsLifetime': False, 'inheritParticleColor': True,
                 'colorOverLifetime': mmg(), 'widthOverTrail': mmc(0, 1.0), 'colorOverTrail': mmg(), 'attachRibbonsToTransform': False,
                 'splitSubEmitterRibbons': False}
        own = p.add('Texture2D', {'m_Name': 'streak', 'm_Width': 4, 'm_Height': 4, 'm_TextureSettings': {'m_WrapU': 1, 'm_WrapV': 1}})
        go, _ = p.system('comet', emitting(TrailModule=trail))
        p.objects[p.objects[go][1]['m_Component'][-1]['component']['m_PathID']][1]['m_Materials'].append(ref(p.add('Material', material_tree(ADDITIVE, own))))
        go2, _ = p.system('streaks', emitting(TrailModule=trail), m_RenderMode=5)
        p.objects[p.objects[go2][1]['m_Component'][-1]['component']['m_PathID']][1]['m_Materials'].append(ref(p.add('Material', material_tree(ADDITIVE, own))))
        p.system('one material', emitting(TrailModule=trail))  # trails without their material draw nothing: only the particles
        tr_go, _ = p.game_object('Trail', p.root, (1, 2, 0))
        p.attach(tr_go, p.add('TrailRenderer', {'m_GameObject': ref(tr_go), 'm_Enabled': 1, 'm_Materials': [ref(p.add('Material', material_tree(ADDITIVE, own)))],
                                                'm_SortingLayerID': 0, 'm_SortingOrder': 0, 'm_Time': 0.3, 'm_MinVertexDistance': 0.1, 'm_Emitting': 1,
                                                'm_Parameters': {'widthMultiplier': 0.3, 'widthCurve': curve([key(0, 1)]), 'colorGradient': gradient(),
                                                                 'numCornerVertices': 0, 'numCapVertices': 0, 'alignment': 0, 'textureMode': 0}}))
        _, doc, layers_doc = p.run()
        by = {s['name']: s for s in doc['systems']}
        self.assertEqual((by['comet']['requires'], by['comet']['render']['mode']), (['trail'], 'billboard'))
        self.assertEqual((by['streaks']['requires'], by['streaks']['render'], by['streaks']['material']), (['trail'], None, None))
        self.assertEqual((by['one material']['requires'], 'trail' in by['one material']), ([], False))
        comet = by['comet']['trail']
        self.assertEqual({k: comet[k] for k in ('mode', 'lifetime', 'textureMode', 'dieWithParticles')},
                         {'mode': 'perParticle', 'lifetime': 0.5, 'textureMode': 'stretch', 'dieWithParticles': True})
        self.assertEqual(doc['materials'][comet['material']]['texture'], doc['materials'][by['streaks']['trail']['material']]['texture'])
        record = doc['trails'][0]
        self.assertEqual((record['name'], record['requires'], record['time'], record['emitter']['matrix'][3]), ('Trail', ['trail'], 0.3, 1))
        # It draws where it sorts: after the second part's... here before draw entry i, after n systems of a run there.
        i, n = record['draw']
        self.assertTrue(i <= len(layers_doc['draw']))
        self.assertEqual(layers_doc['omitted']['trails'], 1, 'layers still count it as not drawn')


class Copies(unittest.TestCase):
    """The tables the exporter, the reference simulator and the validator each keep must agree."""

    def test_the_tables_agree(self):
        mjs = (Path(__file__).resolve().parent.parent / 'scripts' / 'particles.mjs').read_text()
        not_drawn = json.loads(mjs.split('export const NOT_DRAWN = ', 1)[1].split(';', 1)[0].replace("'", '"'))
        self.assertEqual(not_drawn, list(particles.NOT_DRAWN))
        block = mjs.split('export const TIMELINE_COLUMNS = {', 1)[1].split('};', 1)[0]
        widths = {}
        for item in block.replace('\n', ' ').split(','):
            if ':' in item:
                name, width = item.split(':')
                widths[name.strip().strip("'")] = int(width)
        self.assertEqual({**widths, 'main.st': 4}, particles.COLUMN_WIDTHS)
        self.assertEqual({k: v for k, v in particles.COLUMN_WIDTHS.items() if k != 'main.st'}, oracle.COLUMN_WIDTHS)
        self.assertEqual(oracle.FIELD_COLUMNS, tuple(c for c in particles.FIELD_ORDER if c != 'trail.color'))
        self.assertEqual(oracle.STEPPED_COLUMNS, particles.STEPPED)
        self.assertEqual(oracle.MAX_SUB_LINKS, particles.MAX_SUB_LINKS)


class Determinism(unittest.TestCase):
    def test_two_processes_export_the_same_bytes(self):
        # Python salts its string hashes per process (set and dict orders over strings can change): two
        # processes with different salts must still write the same layers.json and layerParticles.json.
        import os
        import subprocess
        script = ('import json, sys; sys.path[:0] = sys.argv[1:4]; import test_particles as t; p = t.ParticlePrefab(); '
                  '[p.system(n, s) for n, s in t.module_systems().items()]; r = p.export(particles=True); '
                  'print(json.dumps([r.document, r.particles], sort_keys=False))')
        here = Path(__file__).resolve().parent
        paths = [str(here), str(here.parent / 'scripts'), str(here.parent / 'scripts' / 'tests')]
        outputs = [subprocess.run([sys.executable, '-c', script, *paths], capture_output=True, text=True, check=True,
                                  env={**os.environ, 'PYTHONHASHSEED': salt}).stdout for salt in ('1', '2')]
        self.assertTrue(outputs[0])
        self.assertEqual(outputs[0], outputs[1])


class RoundTrip(unittest.TestCase):
    """Every module and value mode: simulated from the export as from the typetrees, to 1e-9 (in fact the
    same float64 operations on the same float32 inputs: equal)."""

    def test_every_module_simulates_from_its_export_as_from_its_typetrees(self):
        p = ParticlePrefab()
        trees = {}
        for name, ps in module_systems().items():
            go, _ = p.system(name, ps, position=(0.3, -0.2, 0.1), rotation=(0, 0, 0.38268343, 0.92387953), scale=(1.5, 0.75, 1.0))
            trees[name] = next(tree for kind, tree in p.objects.values() if kind == 'ParticleSystem' and tree['m_GameObject']['m_PathID'] == go)
        _, doc, layers_doc = p.run()
        self.assertEqual(reasons(layers_doc), {})
        self.assertEqual(len(doc['systems']), len(trees))
        for index, system in enumerate(doc['systems']):
            with self.subTest(system['name']):
                seed = oracle.play_seed(7, index, 1)
                pose, _ = oracle.emitter_from_export(system)
                a = oracle.Simulation.from_trees(trees[system['name']], None, seed=seed)
                b = oracle.Simulation.from_export(json.loads(json.dumps(system)), doc, seed=seed)
                self.assertEqual(a.c.prewarm_window, b.c.prewarm_window)
                a.play(pose)
                b.play(pose)
                total = 0
                for _ in range(120):
                    a.step(1 / 60, pose)
                    b.step(1 / 60, pose)
                    ra, rb = records(a), records(b)
                    self.assertEqual(len(ra), len(rb))
                    for x, y in zip(ra, rb):
                        self.assertTrue(all(abs(u - v) <= 1e-9 for u, v in zip(x, y)), (x, y))
                    total += len(ra)
                self.assertGreater(total, 0, 'the system emitted')

    def test_families_mesh_shapes_followers_and_timelines_simulate_from_their_export(self):
        """Sub-emitter families (birth and death, local and world children), a mesh shape with vertex colours,
        a system on a bone follower and one on a timeline with a field column: from the trees and from the
        export, with the same poses (the export's timeline and a moving follower frame) and field values."""
        p = ParticlePrefab()
        mesh = p.add('Mesh', {})
        p.meshes[mesh] = {'vertices': [(0, 0, 0), (2, 0, 0), (0, 1, 0), (2, 1, 0.5)], 'uv': [(0, 0), (1, 0), (0, 1), (1, 1)],
                          'colors': [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1), (1, 1, 1, 0.5)], 'submeshes': [[0, 1, 2, 2, 1, 3]],
                          'normals': [(0, 0, -1), (0, 0.6, -0.8), (0, 0, -1), (0.6, 0, -0.8)]}
        _, hand = p.follower('Hand', p.root, (1, 1, 0))
        parent, parent_tr = p.system('ctrl', emitting(InitialModule__startSpeed=mmc(0, 2.0), InitialModule__startLifetime=mmc(3, 0.4, 0.9),
                                                      EmissionModule__rateOverTime=mmc(0, 6.0), moveWithTransform=1), parent=hand, m_Enabled=0)
        born, _ = p.system('born', emitting(EmissionModule__rateOverTime=mmc(3, 20.0, 40.0), EmissionModule__m_Bursts=[burst(0.0, 2)],
                                            InitialModule__startLifetime=mmc(0, 0.3), moveWithTransform=1), parent=parent_tr)
        died, _ = p.system('died', emitting(EmissionModule__m_Bursts=[burst(0.0, 3), burst(0.1, mmc(3, 1.0, 4.0)['scalar'], probability=0.5)],
                                            InitialModule__startSpeed=mmc(0, 1.0), InitialModule__startLifetime=mmc(0, 0.5), lengthInSec=0.5,
                                            ShapeModule__enabled=True, ShapeModule__type=0, ShapeModule__radius=multi(0.2)),
                            parent=parent_tr, position=(0.5, 0, 0))
        p.link(parent, born)
        p.link(parent, died, kind=2)
        p.system('fromMesh', emitting(**{'ShapeModule__enabled': True, 'ShapeModule__type': 6, 'ShapeModule__m_Mesh': ref(mesh),
                                         'ShapeModule__placementMode': 2, 'ShapeModule__m_MeshSpawn': multi(0.0), 'ShapeModule__m_UseMeshColors': True,
                                         'ShapeModule__m_MeshNormalOffset': 0.1, 'InitialModule__startSpeed': mmc(0, 1.0)}))
        p.system('fromVertices', emitting(**{'ShapeModule__enabled': True, 'ShapeModule__type': 6, 'ShapeModule__m_Mesh': ref(mesh),
                                             'ShapeModule__placementMode': 0, 'ShapeModule__m_MeshSpawn': multi(0.0, mode=1),
                                             'ShapeModule__m_UseMeshColors': True}))
        mover_go, mover = p.game_object('mover', p.root)
        p.system('moved', emitting(moveWithTransform=1, EmissionModule__rateOverDistance=mmc(0, 4.0)), parent=mover)
        p.animate(mover_go, p.clip('move', [{'path': ec.crc('moved'), 'typeID': ec.PARTICLE_SYSTEM, 'attribute': ec.crc('simulationSpeed')},
                                            {'path': 0, 'typeID': ec.TRANSFORM, 'attribute': ec.POSITION}],
                                   [(START, [(0, (0, 0, 0, 1)), (1, (0, 0, 0, 0))]), (0.0, [(0, linear(1, 3, 0, 2)), (1, linear(0, 6, 0, 2))]),
                                    (2.0, [(0, (0, 0, 0, 3)), (1, (0, 0, 0, 6))]), (INF, [])], constants=(0.0, 0.0), stop=2.0))
        _, doc, layers_doc = p.run()
        self.assertEqual(reasons(layers_doc), {})
        names = {s['name']: i for i, s in enumerate(doc['systems'])}
        self.assertEqual(doc['systems'][names['Hand/ctrl']]['render'], None)
        self.assertIn('timeline', doc['systems'][names['mover/moved']]['emitter'])
        mesh_of = lambda reference: p.meshes[reference['m_PathID']]  # noqa: E731

        def frame(t):
            angle = 0.6 * t
            return [math.cos(angle), -math.sin(angle), 0, 1 + t, math.sin(angle), math.cos(angle), 0, 1, 0, 0, 1, 0], (0, 0, math.sin(angle / 2), math.cos(angle / 2))

        def pose(system, t):
            return oracle.emitter_pose(system, t, None, frame(t) if system['follow'] else None)

        total = 0
        for name in ('Hand/ctrl', 'fromMesh', 'fromVertices', 'mover/moved'):
            index = names[name]
            family = [index] + [link[0] for link in doc['systems'][index].get('sub', [])]
            pairs = []
            for k in family:
                system = json.loads(json.dumps(doc['systems'][k]))
                go = next(g for g, (kind, tree) in p.objects.items() if kind == 'GameObject' and tree['m_Name'] == system['name'].split('/')[-1])
                lifetime = max((oracle.MinMaxCurve.from_tree(p.ps_of(g)['InitialModule']['startLifetime']).max_value()
                                for g in (born, died)), default=0.0) if k == index and family[1:] else 0.0
                seed = oracle.play_seed(3, k, 1)
                a = oracle.Simulation(oracle.Config.from_trees(p.ps_of(go), None, child_lifetime=lifetime, mesh_of=mesh_of), seed=seed)
                b = oracle.Simulation.from_export(system, doc, seed=seed)
                self.assertEqual(a.c.prewarm_window, b.c.prewarm_window)
                pairs.append((system, a, b))
            for (_, a, b), link in zip(pairs[1:], doc['systems'][index].get('sub', [])):
                pairs[0][1].link(a, link[1], link[2])
                pairs[0][2].link(b, link[1], link[2])
            for step in range(121):
                t = step / 60
                poses = [pose(system, t) for system, _, _ in pairs]
                for system, a, b in pairs:
                    if 'timeline' in system['emitter']:
                        values = {k: v for k, v in oracle.timeline_values(system['emitter']['timeline'], t).items() if k in oracle.FIELD_COLUMNS}
                        a.set_fields(values)
                        b.set_fields(values)
                for _, a, b in pairs[:1]:
                    if step:
                        a.step(1 / 60, poses[0], poses[1:])
                        b.step(1 / 60, poses[0], poses[1:])
                    else:
                        a.play(poses[0], poses[1:])
                        b.play(poses[0], poses[1:])
                for system, a, b in pairs:
                    ra, rb = records(a), records(b)
                    self.assertEqual(len(ra), len(rb), system['name'])
                    for x, y in zip(ra, rb):
                        self.assertTrue(all(abs(u - v) <= 1e-9 for u, v in zip(x, y)), (system['name'], x, y))
                    total += len(ra)
            if name == 'Hand/ctrl':
                self.assertGreater(len(pairs[1][1].particles) + pairs[2][1].emitted_total, 0, 'the children emitted')
        self.assertGreater(total, 0)

    def test_the_prewarm_window_is_the_longest_lifetime_unless_max_particles_binds(self):
        p = ParticlePrefab()
        systems = module_systems()
        p.system('prewarmed', systems['prewarmed'])
        p.system('capped', systems['capped'])
        _, doc, _ = p.run()
        windows = {s['name']: s['clock']['prewarmWindow'] for s in doc['systems']}
        # 20/s x 1.5 s < 1000: the longest lifetime; 4/s x 0.6 s > 1 particle: min(duration, 30 s).
        self.assertEqual(windows, {'prewarmed': 1.5, 'capped': 5})


if __name__ == '__main__':
    unittest.main()


def hoshiguma_export():
    """Hoshiguma's (char_1044_hsgma2#2) layerParticles.json and layers.json: the committed folder's, or a trial
    export's under $PARTICLE_EXPORT (a root with models/); None when neither has one."""
    import os
    roots = [Path(__file__).resolve().parent.parent] + ([Path(os.environ['PARTICLE_EXPORT'])] if os.environ.get('PARTICLE_EXPORT') else [])
    for root in reversed(roots):
        for path in sorted(root.glob('models/char_1044_hsgma2_2/*/layerParticles.json')):
            return json.loads(path.read_text()), json.loads((path.parent / 'layers.json').read_text())
    return None


@unittest.skipUnless(hoshiguma_export(), 'Hoshiguma has no layerParticles.json here (set PARTICLE_EXPORT to a trial export)')
class Hoshiguma(unittest.TestCase):
    """The reference recordings' facts (particle research RECORDING.md section 6, corrected by RECORDING2.md
    section 4) on Hoshiguma's export: what the idle Animator's Interact state does to the idle particles."""

    @classmethod
    def setUpClass(cls):
        cls.doc, cls.layers = hoshiguma_export()

    def systems(self, under):
        found = [s for s in self.doc['systems'] if f'/{under}/' in s['name']]
        self.assertTrue(found, under)
        return found

    @staticmethod
    def interact(system):
        timeline = system['emitter']['timeline']
        return timeline, timeline['states']['Interact']

    def series(self, system, name, step=1 / 60):
        timeline, line = self.interact(system)
        return [(k * step, oracle.timeline_values(timeline, k * step, 'Interact')[name]) for k in range(int(line['length'] / step) + 1)]

    def switches(self, system):
        timeline, line = self.interact(system)
        out, last = [], None
        for f in line['frames']:
            value = column(timeline, 'active', f)
            if value != last:
                out.append((f[0], value))
                last = value
        return out

    def test_the_gameobject_toggles(self):
        # GameObject: (off from, back on at or None); every system under it switches with it, to within 1/60 s.
        table = {'baoci_01': (6.833, 12.034), 'huaban_dust': (6.867, 12.034), 'fire_foot': (6.833, None), 'water_01': (6.833, 10.867),
                 'fire_back': (6.833, 11.067), 'fire_front': (6.833, 11.467), 'face_fire': (4.067, 11.834)}
        for under, (off, on) in table.items():
            for system in self.systems(under):
                with self.subTest(system['name']):
                    got = self.switches(system)
                    want = [(0, 1), (off, 0)] + ([(on, 1)] if on is not None else [])
                    self.assertEqual([v for _, v in got], [v for _, v in want])
                    for (t, _), (expected, _) in zip(got, want):
                        self.assertLess(abs(t - expected), 1 / 60)
        for system in self.systems('lighting_01'):
            self.assertEqual([v for _, v in self.switches(system)], [0, 1])
            self.assertLess(abs(self.switches(system)[1][0] - 13.067), 1 / 60)
            self.assertEqual({column(self.interact(system)[0], 'active', f) for f in self.interact(system)[1]['frames'] if f[0] < 13}, {0})

    def test_the_idle_tints_fade_out_and_come_back(self):
        # Where the tint's alpha is back at its idle value after the fade (RECORDING section 6), within 0.1 s.
        back = {'water_bg_01_p': 11.5, 'wave_p_01': 11.9, 'wave_p_02': 11.9, 'water_p_01': 12.3, 'water_p_03': 12.3, 'huaban_dust': 12.6,
                'fire_bg_01': 12.97, 'fire_tip_02': 12.97, 'fire_tip_04_3': 13.47, 'drop_01': 13.47, 'baoci_01': 13.3}
        checked = 0
        for system in self.doc['systems']:
            name = system['name'].split('fixed/')[-1]
            key = next((k for k in back if name.endswith(k) or name.startswith(k + '/')), None)
            if key is None or 'timeline' not in system['emitter'] or 'tint' not in system['emitter']['timeline']['columns']:
                continue
            with self.subTest(name):
                alpha = [(t, v[3]) for t, v in self.series(system, 'tint')]
                idle = alpha[0][1]
                fall = next(t for t, v in alpha if v < idle - 0.01)
                zero = next(t for t, v in alpha if v < 0.005)
                self.assertTrue(6.1 < fall < 6.3 and 6.7 < zero < 6.9, (fall, zero))
                returned = next(t for t, v in alpha if t > zero and v >= idle - 0.005)
                self.assertLess(abs(returned - back[key]), 0.1, returned)
                checked += 1
        self.assertGreaterEqual(checked, 20)

    def test_the_simulation_speeds(self):
        # (base, peak, reached the peak at, back at the base at): wave_p, the blades and the small blades.
        table = {'water_01/wave_p_01': (1.0, 1.5, 4.4, 7.1), 'water_01/wave_p_02': (1.0, 1.5, 4.4, 7.1), 'baoci_01/01': (1.0, 1.5, 4.6, 7.53),
                 'baoci_01/02': (1.0, 1.5, 4.6, 7.53), 'baoci_01/baoci_small_01': (0.7, 2.0, 3.7, 7.7), 'baoci_01/baoci_small_02': (0.7, 2.0, 3.7, 7.7)}
        for suffix, (base, peak, reached, returned) in table.items():
            system = next(s for s in self.doc['systems'] if s['name'].endswith(suffix))
            with self.subTest(suffix):
                timeline, _ = self.interact(system)
                at = lambda t: oracle.timeline_values(timeline, t, 'Interact')['speed']  # noqa: E731
                self.assertAlmostEqual(at(0), base, places=3)
                self.assertAlmostEqual(max(v for _, v in self.series(system, 'speed')), peak, places=3)
                self.assertAlmostEqual(at(reached), peak, delta=0.01)
                self.assertAlmostEqual(at(returned), base, delta=0.01)

    def test_coverage(self):
        # 272 systems drawn; all but huaban_fall and its ripple (collision) and the two shield-eye fires
        # (Ram/VertexDisturb) exported, none needing trails.
        drawn = [s for s in self.doc['systems'] if s['render'] is not None]
        left = [r for r in self.layers['omitted']['particleReasons'] if r['reason'] not in particles.NOT_DRAWN]
        self.assertEqual((len(drawn) + len(left), sum(1 for s in drawn if 'trail' not in s['requires'])), (272, 268))
        self.assertEqual(sorted(r['reason'] for r in left), ['Collision module', 'Ram/VertexDisturb on particles', 'Ram/VertexDisturb on particles',
                                                             'the systems it is a sub-emitter of are left out'])
