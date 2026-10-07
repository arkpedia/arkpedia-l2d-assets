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
import layers  # noqa: E402
import particle_oracle as oracle  # noqa: E402
import particles  # noqa: E402
from test_camera import ref  # noqa: E402
from test_layers import ADDITIVE, ALPHA_BLEND, DISTURB, ERASE, Prefab, material_tree  # noqa: E402
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
        p.system('trails', emitting(TrailModule={'enabled': True}))
        p.system('collides', emitting(CollisionModule={'enabled': True}))
        p.system('layer', emitting(), m_SortingLayerID=7)
        p.system('grab', emitting(), material=material_tree(DISTURB, p.texture, keywords='_HGCUSTOMVERTEXSTREAM_ON'))
        go, _ = p.system('follows', emitting())
        p.attach(go, p.add('MonoBehaviour', {'m_Enabled': 1, 'boneName': 'b', 'skeletonRenderer': ref(p.skeleton), 'followXYPosition': 1,
                                              'followBoneRotation': 1, 'followLocalScale': 0}))
        parent_go, parent = p.system('parent', emitting())
        child_go, child = p.system('child', emitting(), parent=parent)
        child_ps = next(pid for pid, (kind, tree) in p.objects.items() if kind == 'ParticleSystem' and tree['m_GameObject']['m_PathID'] == child_go)
        parent_ps = next(tree for kind, tree in p.objects.values() if kind == 'ParticleSystem' and tree['m_GameObject']['m_PathID'] == parent_go)
        parent_ps['SubModule'] = {'enabled': True, 'subEmitters': [{'emitter': ref(child_ps), 'type': 0, 'properties': 0, 'emitProbability': 1.0}]}
        _, doc, layers_doc = p.run()
        self.assertIsNone(doc)  # nothing left to draw
        self.assertEqual(reasons(layers_doc), {
            'off': 'renderer off', 'none': 'render mode None', 'nomaterial': 'no material', 'inactive': 'never active', 'silent': 'emits nothing',
            'trails': 'Trails module', 'collides': 'Collision module', 'layer': 'sorting layer 7',
            'grab': "custom vertex stream keyword without the renderer's streams",
            'follows': 'on a bone follower (not exported yet)', 'parent': 'sub-emitter (not exported yet)',
            'parent/child': 'sub-emitter (not exported yet)'})
        self.assertEqual(layers_doc['omitted']['particles'], 12)
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
