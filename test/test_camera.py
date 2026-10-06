"""Tests for scripts/entrance_camera.py on a synthetic Unity scene shaped like a real entrance.

Run: python -m unittest discover -s test -p 'test_*.py'
"""
import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import entrance_camera as ec  # noqa: E402


def words(frames):
    """A streamed clip's uint32 words from [(time, [(curve, (c0, c1, c2, c3))])], as Unity packs them."""
    data = b''
    for time, keys in frames:
        data += struct.pack('<fi', time, len(keys))
        for index, coefficients in keys:
            data += struct.pack('<i4f', index, *coefficients)
    return list(struct.unpack(f'<{len(data) // 4}I', data))


def linear(v0, v1, t0, t1):
    """Coefficients of a straight line from v0 at t0 to v1 at t1."""
    return (0.0, 0.0, (v1 - v0) / (t1 - t0), v0)


def ref(path_id):
    return {'m_FileID': 0, 'm_PathID': path_id}


def vec(x, y, z):
    return {'x': x, 'y': y, 'z': z}


class Scene:
    """Builds typetrees: a prefab root that plays the skeleton (SkeletonDataAsset scale 0.01),
    an animated camera rig under it, and quads, read through `read`."""

    def __init__(self):
        self.objects = {}
        self.next_id = 100

    def add(self, kind, tree):
        self.next_id += 1
        self.objects[self.next_id] = (kind, tree)
        return self.next_id

    def read(self, path_id):
        return self.objects.get(path_id)

    def game_object(self, name, parent_transform=None, position=(0, 0, 0), rotation=(0, 0, 0, 1), scale=(1, 1, 1), active=1):
        go = self.add('GameObject', {'m_Name': name, 'm_Component': [], 'm_IsActive': active})
        tr = self.add('Transform', {'m_GameObject': ref(go), 'm_Father': ref(parent_transform or 0), 'm_Children': [],
                                    'm_LocalPosition': vec(*position), 'm_LocalRotation': dict(zip('xyzw', rotation)),
                                    'm_LocalScale': vec(*scale)})
        self.attach(go, tr)
        if parent_transform:
            self.objects[parent_transform][1]['m_Children'].append(ref(tr))
        return go, tr

    def attach(self, go, component):
        self.objects[go][1]['m_Component'].append({'component': ref(component)})

    def clip(self, name, bindings, frames, constants=(), stop=10.0):
        count = sum(ec.curve_count(b) for b in bindings) - len(constants)
        return self.add('AnimationClip', {
            'm_Name': name, 'm_Legacy': 0,
            'm_MuscleClip': {'m_StartTime': 0.0, 'm_StopTime': stop, 'm_LoopTime': 0, 'm_Clip': {'data': {
                'm_StreamedClip': {'data': words(frames), 'curveCount': count},
                'm_DenseClip': {'m_FrameCount': 0, 'm_CurveCount': 0, 'm_SampleRate': 30.0, 'm_BeginTime': 0.0, 'm_SampleArray': []},
                'm_ConstantClip': {'data': list(constants)}}}},
            'm_ClipBindingConstant': {'genericBindings': bindings}})

    def animate(self, go, clip):
        controller = self.add('AnimatorController', {'m_AnimationClips': [ref(clip)]})
        self.attach(go, self.add('Animator', {'m_Controller': ref(controller)}))


INF = float('inf')
START = -3.4028234663852886e38


def entrance(roll=False, small_quad=False, perspective=False, no_camera=False):
    """Root (plays the skeleton) > rig (animated: x 0 -> 2, up to the camera) > camera (orthographic
    size 3 -> 4), and a black _TintColor quad over the whole view that fades in and out."""
    s = Scene()
    root_go, root = s.game_object('dyn_entrance_char_9_test#1', position=(8.5, 25.0, 0.0))
    skeleton_data = s.add('MonoBehaviour', {'scale': 0.01, 'skeletonJSON': ref(1), 'atlasAssets': []})
    s.attach(root_go, s.add('MonoBehaviour', {'skeletonDataAsset': ref(skeleton_data), '_animationName': 'Start'}))
    rig_go, rig = s.game_object('03', root, position=(0, 0, 0))
    camera_rotation = (0, 0, math.sin(math.radians(10)), math.cos(math.radians(10))) if roll else (0, 0, 0, 1)
    camera_go, camera_tr = s.game_object('Dummy002', rig, position=(0, 8.0, -6.0))
    cam_go, cam_tr = s.game_object('Main Camera', camera_tr, rotation=camera_rotation)
    camera = s.add('Camera', {'m_GameObject': ref(cam_go), 'orthographic': 0 if perspective else 1,
                              'orthographic size': 3.0, 'field of view': 60.0})
    s.attach(cam_go, camera)
    s.attach(root_go, s.add('MonoBehaviour', {'_params': {'duration': 10.0},
                                               '_mainCamera': {'camera': ref(0 if no_camera else camera)}}))
    scale = (0.5, 0.5, 1) if small_quad else (300, 300, 1)
    quad_go, quad_tr = s.game_object('heip_01', rig, position=(0, 8.0, 0.0), scale=scale)
    mesh = s.add('Mesh', {'m_LocalAABB': {'m_Center': vec(0, 0, 0), 'm_Extent': vec(0.127, 0.127, 0)}})
    s.attach(quad_go, s.add('MeshFilter', {'m_Mesh': ref(mesh)}))
    material = s.add('Material', {'m_SavedProperties': {'m_Colors': [['_TintColor', {'r': 0.0, 'g': 0.0, 'b': 0.0, 'a': 0.0}]]}})
    s.attach(quad_go, s.add('MeshRenderer', {'m_Materials': [ref(material)]}))
    tint = ec.crc('_TintColor') & 0x0FFFFFFF
    bindings = [
        {'path': ec.crc('Dummy002'), 'attribute': ec.POSITION, 'typeID': ec.TRANSFORM},
        {'path': ec.crc('Dummy002/Main Camera'), 'attribute': ec.ORTHOGRAPHIC_SIZE, 'typeID': ec.CAMERA},
        {'path': ec.crc('heip_01'), 'attribute': tint | (7 << 28), 'typeID': ec.RENDERER},
        {'path': ec.crc('heip_01'), 'attribute': tint | (4 << 28), 'typeID': ec.RENDERER},
        {'path': ec.crc('heip_01'), 'attribute': tint | (5 << 28), 'typeID': ec.RENDERER},
        {'path': ec.crc('heip_01'), 'attribute': tint | (6 << 28), 'typeID': ec.RENDERER},
    ]
    # curves: 0-2 rig position, 3 orthographic size, 4 quad alpha (streamed); 5-7 quad rgb (constant 0)
    frames = [
        (START, [(0, (0, 0, 0, 0.0)), (1, (0, 0, 0, 8.0)), (2, (0, 0, 0, -6.0)), (3, (0, 0, 0, 3.0)), (4, (0, 0, 0, 0.0))]),
        (0.0, [(0, linear(0.0, 2.0, 0, 10)), (1, (0, 0, 0, 8.0)), (2, (0, 0, 0, -6.0)), (3, linear(3.0, 4.0, 0, 10)), (4, (0, 0, 0, 0.0))]),
        (4.0, [(4, linear(0.0, 0.5, 4, 5))]),
        (5.0, [(4, linear(0.5, 0.0, 5, 6))]),
        (6.0, [(4, (0, 0, 0, 0.0))]),
        (INF, []),
    ]
    s.animate(rig_go, s.clip('camera', bindings, frames, constants=(0.0, 0.0, 0.0)))
    return s, root_go, skeleton_data


class Clips(unittest.TestCase):
    def test_streamed_frames_round_trip_and_cubic_evaluation(self):
        frames = [(START, [(0, (0, 0, 0, 1.0))]), (1.0, [(0, (1.0, -2.0, 3.0, 4.0))]), (INF, [])]
        parsed = ec.streamed_frames(words(frames))
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[1][1][0][0], 0)
        curve = ec.StreamedCurve()
        for time, keys in parsed[:-1]:
            curve.times.append(time)
            curve.coefficients.append(keys[0][1])
        self.assertEqual(curve.value(0.5), 1.0)          # before the first real key: the -inf key
        self.assertAlmostEqual(curve.value(1.0), 4.0)    # c3 at its own time
        self.assertAlmostEqual(curve.value(2.0), 1 - 2 + 3 + 4)  # ((1 dt - 2) dt + 3) dt + 4 at dt 1

    def test_a_truncated_clip_is_refused(self):
        data = words([(0.0, [(0, (0, 0, 0, 1.0))])])
        with self.assertRaises(ec.CameraError):
            ec.streamed_frames(data[:-1])

    def test_bindings_must_take_exactly_the_curves_the_clip_has(self):
        s = Scene()
        clip = s.clip('broken', [{'path': 1, 'attribute': ec.POSITION, 'typeID': ec.TRANSFORM}], [(0.0, [(0, (0, 0, 0, 1))])])
        tree = s.read(clip)[1]
        tree['m_MuscleClip']['m_Clip']['data']['m_StreamedClip']['curveCount'] = 1
        with self.assertRaisesRegex(ec.CameraError, 'take 3 curves, it has 1'):
            ec.Clip(tree)

    def test_material_binding_splits_the_property_hash_and_channel(self):
        tint = ec.crc('_TintColor')
        self.assertEqual(ec.material_binding(tint), (tint & 0x0FFFFFFF, 2))          # crc32('_TintColor') is its b channel
        self.assertEqual(ec.material_binding((tint & 0x0FFFFFFF) | (7 << 28)), (tint & 0x0FFFFFFF, 3))
        # Measured on Skadi's Red Countess entrance: its _TintColor alpha binding.
        self.assertEqual((tint & 0x0FFFFFFF) | (7 << 28), 1895888582)
        self.assertEqual(ec.ORTHOGRAPHIC_SIZE, 2389637943)
        self.assertEqual(ec.IS_ACTIVE, 2086281974)

    def test_decimation_keeps_turns_and_ends(self):
        samples = [[i / 10, float(i if i <= 10 else 20 - i)] for i in range(21)]
        kept = ec.decimate(samples, [0.01])
        self.assertEqual([k[0] for k in kept], [0.0, 1.0, 2.0])


class Entrance(unittest.TestCase):
    def test_the_camera_comes_out_in_skeleton_units(self):
        scene, root, skeleton = entrance()
        camera = ec.entrance_camera(root, skeleton, scene.read, 10.0)
        frames = camera['frames']
        # A straight pan and zoom decimate to their two ends.
        self.assertEqual(frames, [[0.0, 0.0, 800.0, 600.0], [10.0, 200.0, 800.0, 800.0]])

    def test_a_full_screen_quad_is_a_fade_with_its_tint_doubled(self):
        scene, root, skeleton = entrance()
        fades = ec.entrance_camera(root, skeleton, scene.read, 10.0)['fades']
        self.assertEqual(len(fades), 1)
        self.assertEqual(fades[0]['color'], [0.0, 0.0, 0.0])
        alphas = dict((t, a) for t, a in fades[0]['keys'])
        self.assertEqual(max(alphas.values()), 1.0)  # _TintColor alpha 0.5 draws fully black
        self.assertEqual(alphas[5.0], 1.0)
        self.assertEqual(alphas[0.0], 0.0)

    def test_a_small_quad_is_an_effect_not_a_fade(self):
        scene, root, skeleton = entrance(small_quad=True)
        self.assertEqual(ec.entrance_camera(root, skeleton, scene.read, 10.0)['fades'], [])

    def test_a_rolled_camera_fails_instead_of_being_drawn_wrong(self):
        scene, root, skeleton = entrance(roll=True)
        with self.assertRaisesRegex(ec.CameraError, 'tilts or rolls'):
            ec.entrance_camera(root, skeleton, scene.read, 10.0)

    def test_a_perspective_camera_shows_the_height_its_field_of_view_covers_at_the_skeleton(self):
        scene, root, skeleton = entrance(perspective=True)
        frames = ec.entrance_camera(root, skeleton, scene.read, 10.0)['frames']
        self.assertAlmostEqual(frames[0][3], 2 * 6.0 * math.tan(math.radians(30)) / 0.01, places=0)

    def test_no_camera_named_is_none(self):
        scene, root, skeleton = entrance(no_camera=True)
        self.assertIsNone(ec.entrance_camera(root, skeleton, scene.read, 10.0))

    def test_a_skeleton_nothing_plays_fails(self):
        scene, root, _ = entrance()
        with self.assertRaisesRegex(ec.CameraError, 'plays its skeleton'):
            ec.entrance_camera(root, 999999, scene.read, 10.0)


if __name__ == '__main__':
    unittest.main()
